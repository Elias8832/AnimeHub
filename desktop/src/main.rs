/* Anime Hub desktop wrapper (Decision 26, chromium-engine variant).
   Spawns hub.py as a child (kernel-leashed via PR_SET_PDEATHSIG), waits for
   its port, then opens the UI in a dedicated-profile Chromium app-mode
   window — no tabs, no address bar, own window class. When that window
   closes, the hub is killed: close = quit — unless "keep running after
   closing" or "start at login" is on (Decision 51). Then this process stays,
   without a window, holding the hub on its leash, and a later launch only
   opens a window. Both switches off and no window left: the hub stops.
   `--background` is the same with no window from the start (the login entry).
   While it stays, a tray icon offers Open and Quit.

   Why not a Tauri/webkit2gtk window? Found live on NVIDIA + Wayland
   (RTX 4070 Ti SUPER, Hyprland, 2026-10-17): webkit2gtk's DMABUF renderer
   crashes GTK outright, and every fallback (DMABUF disabled, compositing
   disabled, X11/XWayland, Mesa EGL on the iGPU) renders the app at ~5 fps.
   Chromium provably renders this UI at full speed on the same machine, so
   the wrapper drives it directly. Tauri/webkit are gone from the build.

   A second launch while the app is open just asks Chromium for one more
   app window (the profile's own singleton does the handoff) and exits
   without touching the running hub.

   Windows (TODO 74, Decision 40): the same flow. A Job Object is the
   leash instead of PR_SET_PDEATHSIG, a named mutex the singleton instead
   of flock, %APPDATA% / %LOCALAPPDATA% the folders (as animehub/config.py),
   and the hub runs on the Python bundled beside the exe (python\). No
   console window: errors show in a message box. */
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::fs;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(unix)]
use std::fs::File;
#[cfg(unix)]
use std::os::fd::AsRawFd;
#[cfg(unix)]
use std::os::unix::process::CommandExt;

#[cfg(unix)]
use libc::flock;

#[cfg(unix)]
mod tray;
#[cfg(windows)]
mod windows;

/// Set by the tray's Quit.
pub static QUIT: AtomicBool = AtomicBool::new(false);

/// The per-user data dir: XDG_DATA_HOME, else ~/.local/share (Windows:
/// %LOCALAPPDATA%, as animehub/config.py — an absolute XDG_DATA_HOME
/// still wins there).
fn data_base() -> PathBuf {
    let xdg = std::env::var("XDG_DATA_HOME").ok().map(PathBuf::from).filter(|p| p.is_absolute());
    #[cfg(windows)]
    let xdg = xdg.or_else(|| std::env::var("LOCALAPPDATA").ok().map(PathBuf::from));
    xdg.or_else(|| std::env::var("HOME").ok().map(|h| PathBuf::from(h).join(".local/share")))
        .unwrap_or_else(|| PathBuf::from("/tmp"))
}

/// Last-resort hub.py location: `anime-hub/hub.py` under the user's data
/// dir (XDG_DATA_HOME, else ~/.local/share). Nothing user-specific is baked
/// into the binary.
fn default_hub_py() -> PathBuf {
    data_base().join("anime-hub").join("hub.py")
}

/// Mirror of animehub/config.py's APP_BROWSERS preference order.
#[cfg(unix)]
const CHROMIUM_FAMILY: [&str; 6] = [
    "chromium",
    "chromium-browser",
    "google-chrome-stable",
    "google-chrome",
    "brave",
    "microsoft-edge",
];

/// Windows: the same family by .exe name on PATH, then where each installs
/// (they are rarely on PATH there) — animehub/system.py's _WINDOWS_BROWSERS,
/// same order. Edge comes with Windows.
#[cfg(windows)]
const CHROMIUM_FAMILY: [&str; 4] = ["chromium.exe", "chrome.exe", "brave.exe", "msedge.exe"];
#[cfg(windows)]
const WINDOWS_BROWSERS: [(&str, &str); 8] = [
    ("LOCALAPPDATA", r"Chromium\Application\chrome.exe"),
    ("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"),
    ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
    ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
    ("PROGRAMFILES", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
    ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe"),
];

fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.to_path_buf()))
}

/// Where hub.py lives, in order: ANIME_HUB_PY env (overrides/packaging) →
/// the hub.path file desktop/install.sh writes next to the config → walk
/// up from this executable (running straight from a repo checkout) → the
/// default. None = not found anywhere (caller fails loud instead of
/// opening a window at a dead port — found live on the laptop 2026-10-17:
/// the hardcoded default pointed at a path that only exists on the
/// desktop machine, so the hub never spawned and the window got
/// ERR_CONNECTION_REFUSED). Windows' zip keeps the hub in app\ beside the exe.
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
    #[cfg(windows)]
    if let Some(p) = exe_dir().map(|d| d.join("app").join("hub.py")).filter(|p| p.exists()) {
        return Some(p);
    }
    let mut dir = exe_dir();
    while let Some(d) = dir {
        let cand = d.join("hub.py");
        if cand.exists() {
            return Some(cand);
        }
        dir = d.parent().map(|p| p.to_path_buf());
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
    let base = std::env::var("XDG_CONFIG_HOME").ok().map(PathBuf::from).filter(|p| p.is_absolute());
    #[cfg(windows)]
    let base = base.or_else(|| std::env::var("APPDATA").ok().map(PathBuf::from));
    let base = base.or_else(|| std::env::var("HOME").ok().map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("anime-hub").join("config.json"))
}

fn config_json() -> Option<serde_json::Value> {
    config_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        // A Windows editor may start the file with a BOM (config.py reads utf-8-sig).
        .and_then(|s| serde_json::from_str::<serde_json::Value>(s.trim_start_matches('\u{feff}')).ok())
}

fn configured_port() -> u16 {
    config_json()
        .and_then(|v| v.get("port").and_then(|p| p.as_u64()))
        .map(|p| p as u16)
        .unwrap_or(8787)
}

/// Should the hub stay up without a window? (Decision 51.) "Start at login"
/// counts too: a server started for the phone must not die with the first window.
fn background_wanted() -> bool {
    config_json().is_some_and(|v| {
        ["keep_running", "start_at_login"].iter().any(|k| v.get(k).and_then(|b| b.as_bool()) == Some(true))
    })
}

/// Reads `--background` and `--config PATH` (the login entry's arguments).
fn read_args() -> bool {
    let args: Vec<String> = std::env::args().collect();
    if let Some(cfg) = args.iter().position(|a| a == "--config").and_then(|i| args.get(i + 1)) {
        std::env::set_var("ANIME_HUB_CONFIG", cfg);
    }
    args.iter().any(|a| a == "--background")
}

/// What a login entry should run: the AppImage when this is one (the path
/// inside it changes every start), else this program.
fn launcher() -> Option<PathBuf> {
    std::env::var_os("APPIMAGE").map(PathBuf::from).or_else(|| std::env::current_exe().ok())
}

/// With no window of this process left: hold the hub while it's wanted, and
/// stop it when neither switch is on and no other launch shows a window.
fn in_background(hub: &mut Option<Child>, window_open: impl Fn() -> bool) {
    let mut icon = None;
    loop {
        let wanted = background_wanted();
        if QUIT.load(Ordering::Relaxed) || (!wanted && !window_open()) {
            kill_hub(hub); // close = quit
            return;
        }
        if !hub.as_mut().is_some_and(|child| matches!(child.try_wait(), Ok(None))) {
            return;
        }
        if wanted && icon.is_none() {
            #[cfg(unix)]
            let shown = tray::show();
            #[cfg(windows)]
            let shown = windows::show_tray();
            icon = shown;
        }
        thread::sleep(Duration::from_millis(500));
    }
}

/// A later launch's window has nothing left to show once the hub is gone
/// (the tray's Quit): wait for it to close, or close it. Only after the hub
/// answered once, so a slow or failed start keeps the window and its message.
fn wait_for_window(win: &mut Child, port: u16) {
    let (mut seen, mut gone) = (false, 0);
    while matches!(win.try_wait(), Ok(None)) {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            (seen, gone) = (true, 0);
        } else if seen {
            gone += 1;
            if gone >= 2 {
                close_window(win);
                break;
            }
        }
        thread::sleep(Duration::from_secs(1));
    }
    let _ = win.wait();
}

/// Asks the browser to end, so it doesn't report a crash next time.
#[cfg(unix)]
fn close_window(win: &mut Child) {
    unsafe { libc::kill(win.id() as i32, libc::SIGTERM) };
}

#[cfg(windows)]
fn close_window(win: &mut Child) {
    let _ = win.kill();
}

/// The tray's Open: a launch like any other, so it opens the window the
/// usual way. It lives as long as its window, not as long as this process.
pub fn open_window() {
    let Some(mut child) = launcher().and_then(|path| Command::new(path).spawn().ok()) else {
        return;
    };
    thread::spawn(move || child.wait());
}

#[cfg(unix)]
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

/// A program on PATH (Windows: by its full name, e.g. "msedge.exe").
#[cfg(windows)]
fn on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|d| d.join(name)).find(|p| p.is_file())
}

#[cfg(windows)]
fn chromium_exe() -> Option<String> {
    CHROMIUM_FAMILY
        .iter()
        .find_map(|name| on_path(name))
        .or_else(|| {
            WINDOWS_BROWSERS.iter().find_map(|(var, rel)| {
                let p = PathBuf::from(std::env::var_os(var)?).join(rel);
                p.is_file().then_some(p)
            })
        })
        .map(|p| p.display().to_string())
}

/// The app window's browser profile. Windows keeps one per port: two Anime
/// Hubs (another config, another port) sharing one profile handed the
/// second app's window to the first app's browser, at a port nobody
/// served (found on Windows, TODO 74). The default port keeps the old name.
fn wrapper_profile(port: u16) -> PathBuf {
    let dir = data_base().join("anime-hub-wrapper");
    if cfg!(windows) && port != 8787 { dir.join(format!("chrome-profile-{port}")) } else { dir.join("chrome-profile") }
}

/// Spawn hub.py with --no-browser (the wrapper owns the window) and with
/// PR_SET_PDEATHSIG: the kernel kills the child the instant this process
/// dies — clean close, crash, SIGKILL, anything — so an orphaned hub
/// silently holding the port can never happen (found live: a wrapper exit
/// that skipped the graceful path left the hub serving).
#[cfg(unix)]
fn spawn_hub(hub_py: &PathBuf) -> Option<Child> {
    let result = unsafe {
        let mut cmd = Command::new("python3");
        cmd.arg(hub_py).arg("--no-browser");
        if let Some(path) = launcher() {
            cmd.env("ANIME_HUB_WRAPPER", path);
        }
        // The port was read from ANIME_HUB_CONFIG's file (configured_port):
        // the hub must read the same file, or the window waits on another port.
        if let Ok(cfg) = std::env::var("ANIME_HUB_CONFIG") {
            cmd.arg("--config").arg(cfg);
        }
        cmd
            .pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0);
                Ok(())
            })
            .spawn()
    };
    result.ok()
}

/// Windows: the Python to run the hub with — $ANIME_HUB_PYTHON, the one
/// bundled beside the exe (python\python.exe, Decision 40), else
/// python.exe on PATH for a repo checkout (never the Microsoft Store's
/// stub in WindowsApps, which only opens the Store).
#[cfg(windows)]
fn python_exe() -> Option<PathBuf> {
    std::env::var_os("ANIME_HUB_PYTHON")
        .map(PathBuf::from)
        .filter(|p| p.is_file())
        .or_else(|| exe_dir().map(|d| d.join("python").join("python.exe")).filter(|p| p.is_file()))
        .or_else(|| on_path("python.exe").filter(|p| !p.to_string_lossy().to_lowercase().contains("windowsapps")))
}

/// Windows: where the hub's output goes — anime-hub-wrapper\hub.log, one per
/// port like the profile (hub-<port>.log). Each start appends under a
/// header, so a failed first start isn't lost to the next one.
#[cfg(windows)]
fn hub_log(port: u16) -> PathBuf {
    let name = if port == 8787 { "hub.log".to_string() } else { format!("hub-{port}.log") };
    wrapper_profile(port).with_file_name(name)
}

/// Windows: the hub runs without a console window; what it prints goes to
/// hub_log. UTF-8 mode, so text
/// files and the log never fall back to the ANSI code page. The Job Object
/// (windows::Leash) is the PR_SET_PDEATHSIG here.
#[cfg(windows)]
fn spawn_hub(python: &PathBuf, hub_py: &PathBuf, port: u16, leash: &windows::Leash) -> Option<Child> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let path = hub_log(port);
    if fs::metadata(&path).is_ok_and(|m| m.len() > 256 * 1024) {
        let _ = fs::remove_file(&path);
    }
    let log = fs::OpenOptions::new().create(true).append(true).open(&path).ok();
    if let Some(mut f) = log.as_ref() {
        use std::io::Write;
        let _ = writeln!(f, "=== {} start (wrapper {})", windows::now(), std::process::id());
    }
    let mut cmd = Command::new(python);
    cmd.arg(hub_py).arg("--no-browser");
    if let Some(path) = launcher() {
        cmd.env("ANIME_HUB_WRAPPER", path);
    }
    if let Ok(cfg) = std::env::var("ANIME_HUB_CONFIG") {
        cmd.arg("--config").arg(cfg);
    }
    if let Some(log) = log {
        if let Ok(err) = log.try_clone() {
            cmd.stdout(log).stderr(err);
        }
    }
    let child = cmd
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .ok()?;
    leash.hold(&child);
    Some(child)
}

#[cfg(unix)]
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

/// Windows: a Chromium-family browser runs on this profile — it holds
/// <profile>\lockfile open without sharing, and deletes it when it ends.
#[cfg(windows)]
fn profile_in_use(profile: &std::path::Path) -> bool {
    let lock = profile.join("lockfile");
    lock.exists() && fs::OpenOptions::new().write(true).open(&lock).is_err()
}

/// Windows: does an Anime Hub answer on this port? (Not just anything: a
/// port taken by another program accepts connections too.)
#[cfg(windows)]
fn hub_answers(port: u16) -> bool {
    use std::io::{Read, Write};
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
    let req = format!("GET /api/now HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n");
    let mut reply = Vec::new();
    s.write_all(req.as_bytes()).is_ok()
        && s.take(65536).read_to_end(&mut reply).is_ok()
        && String::from_utf8_lossy(&reply).contains("\"playing\"")
}

/// Windows: wait until an Anime Hub answers on the port. `own` is the hub
/// this launch started: if it ends first (and no other Anime Hub answers —
/// one started by hand is used, as on Linux), a message box shows the end
/// of its log. Nothing answering within `secs` gets a message box too: a
/// window is never opened at a port where no Anime Hub answers, since that
/// error page doesn't recover by itself (found on Windows, TODO 74).
#[cfg(windows)]
fn wait_for_hub(port: u16, mut own: Option<&mut Child>, secs: u64) {
    let start = Instant::now();
    loop {
        if hub_answers(port) {
            trace(port, &format!("hub answers after {:.1} s", start.elapsed().as_secs_f32()));
            return;
        }
        let ended = own.as_mut().is_some_and(|c| c.try_wait().ok().flatten().is_some());
        if ended && !hub_answers(port) {
            trace(port, &format!("hub ended after {:.1} s without answering", start.elapsed().as_secs_f32()));
            fail(&format!("Anime Hub's server stopped while starting.\n\n{}", log_tail(port)));
        }
        if start.elapsed() > Duration::from_secs(secs) {
            trace(port, &format!("no answer within {secs} s"));
            fail(&format!("Anime Hub's server did not answer within {secs} seconds.\n\n{}", log_tail(port)));
        }
        thread::sleep(Duration::from_millis(200));
    }
}

/// Windows: the last lines of the hub's log and where the whole log is.
#[cfg(windows)]
fn log_tail(port: u16) -> String {
    let log = fs::read_to_string(hub_log(port)).unwrap_or_default();
    let lines: Vec<&str> = log.lines().collect();
    let tail = lines[lines.len().saturating_sub(8)..].join("\n");
    format!("{tail}\n\nFull log: {}", hub_log(port).display())
}

/// Windows: one line in anime-hub-wrapper\wrapper.log — when and why this
/// launch did what it did (start-up problems happen on other people's PCs).
#[cfg(windows)]
fn trace(port: u16, msg: &str) {
    use std::io::Write;
    let path = wrapper_profile(port).with_file_name("wrapper.log");
    if fs::metadata(&path).is_ok_and(|m| m.len() > 256 * 1024) {
        let _ = fs::remove_file(&path);
    }
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} [{}] port {port}: {msg}", windows::now(), std::process::id());
    }
}

/// Kill the hub child gracefully (PDEATHSIG is the crash backstop).
fn kill_hub(hub: &mut Option<Child>) {
    if let Some(mut child) = hub.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// The window's class on native Wayland: chromium ignores --class there
/// and names an app window chrome-<host>__-<profile> (tested 2026-10-22,
/// Chromium 155). desktop/install.sh writes the same into StartupWMClass.
#[cfg(unix)]
pub const WAYLAND_CLASS: &str = "chrome-127.0.0.1__-Default";

/// Native Wayland when the session has it (TODO 61: XWayland scrolled
/// choppily on NVIDIA + a 240 Hz screen; Wayland is smooth), else X11.
#[cfg(unix)]
fn ozone_platform() -> &'static str {
    if std::env::var_os("WAYLAND_DISPLAY").is_some() { "wayland" } else { "x11" }
}

fn app_window_cmd(exe: &str, port: u16) -> Command {
    let mut cmd = Command::new(exe);
    cmd.arg(format!("--app=http://127.0.0.1:{port}/?shell=1"))
        .arg(format!("--user-data-dir={}", wrapper_profile(port).display()))
        .arg("--window-size=1440,900")
        .arg("--new-window")
        .arg("--no-first-run")
        .arg("--no-default-browser-check");
    cmd
}

#[cfg(unix)]
fn app_window(exe: &str, port: u16) -> Child {
    let result = unsafe {
        app_window_cmd(exe, port)
            // X11: WM_CLASS "AnimeHub". Wayland ignores it (WAYLAND_CLASS).
            .arg("--class=AnimeHub")
            .arg(format!("--ozone-platform={}", ozone_platform()))
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

/// Windows: the same window on the same leash as the hub.
#[cfg(windows)]
fn app_window(exe: &str, port: u16, leash: &windows::Leash) -> Child {
    match app_window_cmd(exe, port).spawn() {
        Ok(child) => {
            leash.hold(&child);
            child
        }
        Err(e) => fail(&format!("Anime Hub could not open its window ({exe}): {e}")),
    }
}

/// A fatal start-up error: printed on Linux, a message box on Windows
/// (there is no console to print to).
fn fail(msg: &str) -> ! {
    #[cfg(unix)]
    eprintln!("anime-hub: {msg}");
    #[cfg(windows)]
    windows::error_box(msg);
    std::process::exit(1);
}

/// A later launch holds this (shared) while its window is open, so the
/// launch that owns the hub can see that a window still needs it.
#[cfg(unix)]
fn hold_window_lock(dir: &std::path::Path) -> Option<File> {
    let file = File::create(dir.join("window.lock")).ok()?;
    (unsafe { flock(file.as_raw_fd(), libc::LOCK_SH) } == 0).then_some(file)
}

#[cfg(unix)]
fn window_lock_held(dir: &std::path::Path) -> bool {
    File::create(dir.join("window.lock"))
        .is_ok_and(|f| unsafe { flock(f.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0)
}

#[cfg(unix)]
fn main() {
    let background = read_args();
    let port = configured_port();
    let exe = match chromium_exe() {
        Some(e) => e,
        None if background => String::new(), // no window to open
        None => fail(&format!(
            "no Chromium-family browser found (tried {}). \
             Install chromium, or start the hub directly: python3 <path-to>/hub.py",
            CHROMIUM_FAMILY.join(", ")
        )),
    };
    let hub_py = match find_hub_py() {
        Some(p) => p,
        None => fail(&format!(
            "hub.py not found (looked via $ANIME_HUB_PY, \
             hub.path next to the config, up from this executable, and {}). \
             Install the app or set ANIME_HUB_PY.",
            default_hub_py().display()
        )),
    };

    // Singleton lock (flock releases automatically on process death). A
    // second launch while the app is open must NOT wait on its own
    // chromium process: with a shared profile that process hands the window
    // to the running instance and exits immediately — treating that as
    // "window closed" would kill the hub under a live app window.
    let dir = wrapper_profile(port).parent().unwrap().to_path_buf();
    let _ = fs::create_dir_all(&dir);
    let lock = File::create(dir.join("wrapper.lock")).expect("lock file");
    let first = unsafe { flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0;

    if background {
        // Already running: nothing to do. No window, whatever happens.
        if first {
            let mut hub = spawn_hub(&hub_py);
            in_background(&mut hub, || window_lock_held(&dir));
        }
    } else if first {
        let mut hub = spawn_hub(&hub_py);
        wait_for_port(port, 15);
        let mut win = app_window(&exe, port);
        let _ = win.wait();
        in_background(&mut hub, || window_lock_held(&dir));
    } else {
        // The first launch may still be starting its hub (a login entry).
        wait_for_port(port, 15);
        let _held = hold_window_lock(&dir);
        // This chromium hands the window to the running instance's profile
        // singleton and exits. Wait for that: returning now would fire its
        // PR_SET_PDEATHSIG and kill it before the handoff.
        let mut win = app_window(&exe, port);
        wait_for_window(&mut win, port);
    }
}

#[cfg(windows)]
fn main() {
    let background = read_args();
    let port = configured_port();
    let exe = chromium_exe().unwrap_or_else(|| {
        if background {
            return String::new(); // no window to open
        }
        fail("No Chromium-family browser found (Edge, Chrome, Brave or Chromium). \
              Microsoft Edge comes with Windows — reinstall it, or install Chrome.")
    });
    let leash = windows::Leash::new()
        .unwrap_or_else(|| fail("Anime Hub could not set up its process group (Job Object)."));

    // Singleton per port (the mutex lives until this process exits, like the
    // flock): a second launch only asks the running window's browser for
    // another window, and waits for that hand-off — it needs no hub or Python.
    let dir = wrapper_profile(port).parent().unwrap().to_path_buf();
    let _ = fs::create_dir_all(&dir);
    let first = windows::first_instance(port);
    trace(port, &format!("launch {} ({})", if first { "first" } else { "second" },
                         std::env::current_exe().map(|e| e.display().to_string()).unwrap_or_default()));
    if first {
        let hub_py = find_hub_py().unwrap_or_else(|| {
            fail(&format!(
                "hub.py was not found (looked in app\\ beside this program, $ANIME_HUB_PY, \
                 hub.path next to the config, and {}). Unpack the whole Anime Hub folder again.",
                default_hub_py().display()
            ))
        });
        let python = python_exe().unwrap_or_else(|| {
            fail("Python was not found (python\\python.exe beside this program). \
                  Unpack the whole Anime Hub folder again.")
        });
        let mut hub = spawn_hub(&python, &hub_py, port, &leash);
        match hub.as_ref() {
            Some(child) => trace(port, &format!("hub started (pid {}, {})", child.id(), python.display())),
            None => fail(&format!("Anime Hub could not start its server ({}).", python.display())),
        }
        wait_for_hub(port, hub.as_mut(), 120);
        let profile = wrapper_profile(port);
        if background {
            trace(port, "started without a window");
            in_background(&mut hub, || profile_in_use(&profile));
            trace(port, "stopping the hub");
            return;
        }
        let mut win = app_window(&exe, port, &leash);
        trace(port, &format!("window opened (pid {})", win.id()));
        let opened = Instant::now();
        let mut held = false; // a browser held the profile while ours ran
        while win.try_wait().ok().flatten().is_none() {
            held = held || profile_in_use(&profile);
            thread::sleep(Duration::from_millis(200));
        }
        trace(port, &format!("browser process ended after {:.1} s", opened.elapsed().as_secs_f32()));
        // The browser we started need not be the one showing the window: Edge
        // sometimes starts a fresh browser and quits within milliseconds (seen
        // on Elias's PC, never reproduced: the old wrapper took that for "window
        // closed" and stopped the hub under a window that then said "refused"),
        // and a launch right behind this one can get there first. So the hub
        // follows the profile: if no browser held it while ours ran, ours
        // handed off — give that up to 20 s to show up; then the hub lives as
        // long as any browser holds the profile.
        if !held {
            let handoff = Instant::now();
            while !profile_in_use(&profile) && handoff.elapsed() < Duration::from_secs(20) {
                thread::sleep(Duration::from_millis(200));
            }
            trace(port, &format!("profile in use: {} (waited {:.1} s)", profile_in_use(&profile),
                                 handoff.elapsed().as_secs_f32()));
        }
        while profile_in_use(&profile) {
            thread::sleep(Duration::from_millis(500));
        }
        trace(port, "window closed");
        in_background(&mut hub, || profile_in_use(&profile));
        trace(port, "stopping the hub");
    } else if !background {
        wait_for_hub(port, None, 120); // the first launch may still be starting its hub
        let mut win = app_window(&exe, port, &leash);
        trace(port, &format!("window asked for (pid {})", win.id()));
        wait_for_window(&mut win, port);
    }
}
