/* Anime Hub desktop wrapper (Decision 26, chromium-engine variant).
   Spawns hub.py as a child (kernel-leashed via PR_SET_PDEATHSIG), waits for
   its port, then opens the UI in a dedicated-profile Chromium app-mode
   window — no tabs, no address bar, own window class. When that window
   closes, the hub is killed: close = quit.

   Why not a Tauri/webkit2gtk window? Found live on NVIDIA + Wayland
   (RTX 4070 Ti SUPER, Hyprland, 2026-10-17): webkit2gtk's DMABUF renderer
   crashes GTK outright, and every fallback (DMABUF disabled, compositing
   disabled, X11/XWayland, Mesa EGL on the iGPU) renders the app at ~5 fps.
   Chromium provably renders this UI at full speed on the same machine, so
   the wrapper drives it directly. Tauri/webkit are gone from the build.

   A second launch while the app is open just asks Chromium for one more
   app window (the profile's own singleton does the handoff) and exits
   without touching the running hub. */

use std::fs::{self, File};
use std::net::TcpStream;
use std::os::fd::AsRawFd;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::thread;
use std::time::{Duration, Instant};

use libc::flock;

/// Last-resort hub.py location: `anime-hub/hub.py` under the user's data
/// dir (XDG_DATA_HOME, else ~/.local/share). Nothing user-specific is baked
/// into the binary.
fn default_hub_py() -> PathBuf {
    let base = std::env::var("XDG_DATA_HOME")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| std::env::var("HOME").ok().map(|h| PathBuf::from(h).join(".local/share")))
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    base.join("anime-hub").join("hub.py")
}

/// Mirror of animehub/config.py's APP_BROWSERS preference order.
const CHROMIUM_FAMILY: [&str; 6] = [
    "chromium",
    "chromium-browser",
    "google-chrome-stable",
    "google-chrome",
    "brave",
    "microsoft-edge",
];

/// Where hub.py lives, in order: ANIME_HUB_PY env (overrides/packaging) →
/// the hub.path file desktop/install.sh writes next to the config → walk
/// up from this executable (running straight from a repo checkout) → the
/// default. None = not found anywhere (caller fails loud instead of
/// opening a window at a dead port — found live on the laptop 2026-10-17:
/// the hardcoded default pointed at a path that only exists on the
/// desktop machine, so the hub never spawned and the window got
/// ERR_CONNECTION_REFUSED).
fn find_hub_py() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("ANIME_HUB_PY") {
        let p = PathBuf::from(p);
        if p.exists() {
            return Some(p);
        }
    }
    if let Some(cfg) = config_path() {
        let pf = cfg.with_file_name("hub.path");
        if let Ok(s) = fs::read_to_string(pf) {
            let p = PathBuf::from(s.trim());
            if p.exists() {
                return Some(p);
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|d| d.to_path_buf());
        while let Some(d) = dir {
            let cand = d.join("hub.py");
            if cand.exists() {
                return Some(cand);
            }
            dir = d.parent().map(|p| p.to_path_buf());
        }
    }
    let def = default_hub_py();
    if def.exists() {
        return Some(def);
    }
    None
}

fn config_path() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("ANIME_HUB_CONFIG") {
        return Some(PathBuf::from(p));
    }
    let base = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| std::env::var("HOME").ok().map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("anime-hub").join("config.json"))
}

fn configured_port() -> u16 {
    config_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("port").and_then(|p| p.as_u64()))
        .map(|p| p as u16)
        .unwrap_or(8787)
}

fn chromium_exe() -> Option<String> {
    let path = std::env::var("PATH").unwrap_or_default();
    CHROMIUM_FAMILY
        .iter()
        .find(|name| {
            path.split(':')
                .any(|dir| fs::metadata(PathBuf::from(dir).join(name)).is_ok())
        })
        .map(|s| s.to_string())
}

fn wrapper_profile() -> PathBuf {
    let base = std::env::var("XDG_DATA_HOME")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| std::env::var("HOME").ok().map(|h| PathBuf::from(h).join(".local/share")))
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    base.join("anime-hub-wrapper").join("chrome-profile")
}

/// Spawn hub.py with --no-browser (the wrapper owns the window) and with
/// PR_SET_PDEATHSIG: the kernel kills the child the instant this process
/// dies — clean close, crash, SIGKILL, anything — so an orphaned hub
/// silently holding the port can never happen (found live: a wrapper exit
/// that skipped the graceful path left the hub serving).
fn spawn_hub(hub_py: &PathBuf) -> Option<Child> {
    let result = unsafe {
        Command::new("python3")
            .arg(hub_py)
            .arg("--no-browser")
            .pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0);
                Ok(())
            })
            .spawn()
    };
    result.ok()
}

fn wait_for_port(port: u16, secs: u64) {
    let deadline = Instant::now() + Duration::from_secs(secs);
    loop {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return;
        }
        if Instant::now() >= deadline {
            return; // open anyway — the UI's own "Can't reach the hub" recovery takes over
        }
        thread::sleep(Duration::from_millis(150));
    }
}

/// Kill the hub child gracefully (PDEATHSIG is the crash backstop).
fn kill_hub(hub: &mut Option<Child>) {
    if let Some(mut child) = hub.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn app_window(exe: &str, port: u16) -> Child {
    let result = unsafe {
        Command::new(exe)
            .arg(format!("--app=http://127.0.0.1:{port}/?shell=1"))
            .arg(format!("--user-data-dir={}", wrapper_profile().display()))
            .arg("--class=AnimeHub")
            .arg("--window-size=1440,900")
            .arg("--new-window")
            .arg("--no-first-run")
            .arg("--no-default-browser-check")
            // X11 backend: --class (WM_CLASS) is honored there, giving the
            // window a clean "AnimeHub" identity for docks/window rules. On
            // native Wayland chromium ignores --class and reports a generic
            // chrome-<host>__-<profile> app_id instead.
            .arg("--ozone-platform=x11")
            // Same kernel leash as the hub: when the wrapper dies, the
            // window must die with it — a leftover app window otherwise
            // hands off to the profile singleton on the next launch and
            // confuses everything (found live).
            .pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0);
                Ok(())
            })
            .spawn()
    };
    result.expect("chromium-family browser spawns")
}

fn main() {
    let port = configured_port();
    let exe = match chromium_exe() {
        Some(e) => e,
        None => {
            eprintln!(
                "anime-hub: no Chromium-family browser found (tried {}). \
                 Install chromium, or start the hub directly: python3 <path-to>/hub.py",
                CHROMIUM_FAMILY.join(", ")
            );
            std::process::exit(1);
        }
    };
    let hub_py = match find_hub_py() {
        Some(p) => p,
        None => {
            eprintln!(
                "anime-hub: hub.py not found (looked via $ANIME_HUB_PY, \
                 hub.path next to the config, up from this executable, and {}). \
                 Install the app or set ANIME_HUB_PY.",
                default_hub_py().display()
            );
            std::process::exit(1);
        }
    };

    // Singleton lock (flock releases automatically on process death). A
    // second launch while the app is open must NOT wait on its own
    // chromium process: with a shared profile that process hands the window
    // to the running instance and exits immediately — treating that as
    // "window closed" would kill the hub under a live app window.
    let dir = wrapper_profile().parent().unwrap().to_path_buf();
    let _ = fs::create_dir_all(&dir);
    let lock = File::create(dir.join("wrapper.lock")).expect("lock file");
    let first = unsafe { flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0;

    if first {
        let mut hub = spawn_hub(&hub_py);
        wait_for_port(port, 15);
        let mut win = app_window(&exe, port);
        let _ = win.wait();
        kill_hub(&mut hub); // close = quit
    } else {
        let _ = app_window(&exe, port);
    }
}
