"""The Android app (Decision 88): the hub runs inside the app, and ani-cli
runs in the user's Termux.

An Android app can't start Termux's programs, but Termux takes jobs from
other apps (RUN_COMMAND). So each ani-cli run becomes one Termux job: the
helper scripts the hub wrote (the menu helper, the player stand-in) are
recreated in a Termux temp folder, ani-cli runs there with the hub's
settings, and the files it wrote come back. The hub's callers get an object
that behaves like the Popen they always had. The app holds no provider
code: ani-cli does the finding, as on the PC (Decision 1).

Its spots elsewhere check system.ANDROID; tests swap `transport` for a
fake Termux that runs the job with the local bash."""

import base64
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .config import config

TERMUX_PREFIX = "/data/data/com.termux/files/usr"
# What a hub-written script's "#!/bin/sh" becomes in Termux: Android has no
# /bin/sh, and a RUN_COMMAND job can't count on termux-exec to fix it.
TERMUX_SH = TERMUX_PREFIX + "/bin/sh"
BRIDGE_CLASS = "io.github.elias8832.animehub.TermuxBridge"
JOB_TIMEOUT = 180  # ani-cli's own startup limit is 90 s; this is the backstop
FETCH_TIMEOUT = 10

# The fixes, in the words the page shows. ani-cli's die() style (bold red)
# so anicli_message picks them out of the job's stderr.
NO_TERMUX = ("Watching online needs Termux with ani-cli. Install Termux from F-Droid or GitHub "
             "(not the Play Store), then run in it: pkg upgrade -y && pkg install -y ani-cli")
NO_ANICLI = "ani-cli isn't installed in Termux yet. Run in Termux: pkg upgrade -y && pkg install -y ani-cli"
NOT_ALLOWED = ("Termux doesn't let other apps use it yet. Run in Termux: mkdir -p ~/.termux && echo "
               "allow-external-apps=true >> ~/.termux/termux.properties && termux-reload-settings")
NOT_STARTED = "Android didn't let Termux start. Open Termux once, then try again."
NO_PERMISSION = ("AnimeHub may not use Termux yet. Allow it in Android's settings: Apps → AnimeHub → "
                 "Permissions → Run commands in Termux environment.")


def _java_transport(script, timeout):
    from java import jclass  # Chaquopy, only inside the app
    bridge = jclass(BRIDGE_CLASS)
    rid = bridge.run(script)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = bridge.result(rid)
        if r is not None:
            out, err, rc, msg = (str(x) for x in r)
            return int(rc), out, err, msg
        time.sleep(0.1)
    return -1, "", "", "Termux did not answer within %d s" % timeout


# (script, timeout) → (exit code, stdout, stderr, Termux's error text).
transport = _java_transport


def termux_error(msg, err=""):
    """Termux's own refusal, in the page's words; "" when there was none."""
    m = (msg + " " + err).lower()
    if "allow-external-apps" in m:
        return NOT_ALLOWED
    if "no-permission" in m or "permission denial" in m or "securityexception" in m:
        return NO_PERMISSION
    if "no-termux" in m or "unable to start service" in m or "not found" in m and "termux" in m:
        return NO_TERMUX
    if "not allowed to start service" in m or "background start" in m:
        return NOT_STARTED
    if "did not answer" in m:
        return msg
    return ""


def _red(text):
    return ("\x1b[1;31m%s\x1b[0m\n" % text).encode()


def _q(s):
    return shlex.quote(str(s))


def _ours(value):
    """A file the hub made for this run: its data folder or temp files."""
    try:
        p = Path(value)
        if not p.is_absolute():
            return False
        p = p.resolve()
    except (OSError, ValueError):
        return False
    for root in (config.DATA, Path(tempfile.gettempdir())):
        try:
            p.relative_to(Path(root).resolve())
            return True
        except (OSError, ValueError):
            pass
    return False


def job_script(args, env, want_stdout):
    """The bash script one ani-cli run becomes, and [(local path, name)]
    of the files to bring back."""
    lines = ['T=$(mktemp -d "${TMPDIR:-/tmp}/animehub.XXXXXX") || exit 97', 'cd "$T"']
    files = []
    for k, v in env.items():
        if not (k.startswith("ANI_CLI_") or k.startswith("HUB_ANICLI_") or k == "TERM"):
            continue  # the app's own environment means nothing in Termux
        if not _ours(v):
            lines.append("export %s=%s" % (k, _q(v)))
            continue
        # Keep the file's name: ani-cli picks its player by it (*mpv*).
        name = "%d-%s" % (len(files) + 1, os.path.basename(str(v)))
        try:
            data = Path(v).read_bytes() if Path(v).is_file() else b""
        except OSError:
            data = b""
        if data:
            data = re.sub(rb"\A#!/bin/sh\b", b"#!" + TERMUX_SH.encode(), data)
            lines.append("printf %%s %s | base64 -d > %s && chmod +x %s" % (
                _q(base64.b64encode(data).decode()), _q(name), _q(name)))
        files.append((str(v), name))
        lines.append('export %s="$T/"%s' % (k, _q(name)))
    lines.append('command -v ani-cli >/dev/null || { echo "@@NOANICLI"; cd /; rm -rf "$T"; exit 0; }')
    lines.append('ani-cli %s </dev/null >"$T/@stdout" 2>"$T/@stderr"; echo "@@RC $?"'
                 % " ".join(_q(a) for a in args))
    back = files + ([(None, "@stdout")] if want_stdout else []) + [(None, "@stderr")]
    for _, name in back:
        lines.append('if [ -e %s ]; then echo "@@FILE %s"; base64 -w0 %s; echo; fi' % (_q(name), name, _q(name)))
    lines.append('cd /; rm -rf "$T"')
    return "\n".join(lines), files


def parse_job(out):
    """(ani-cli's exit code or None, {name: bytes}, ani-cli missing)."""
    m = re.search(r"^@@RC (-?\d+)$", out, re.M)
    files = {name: base64.b64decode(b64)
             for name, b64 in re.findall(r"^@@FILE (\S+)\n([A-Za-z0-9+/=]*)$", out, re.M)}
    return (int(m[1]) if m else None), files, "@@NOANICLI" in out


class TermuxRun:
    """One ani-cli run in Termux, shaped like the Popen anicli_spawn used
    to return. pid is None, so stop_process_group falls back to
    send_signal, which can't reach a Termux job: it ends by itself."""

    def __init__(self, args, env, stdout, stderr):
        self.pid = None
        self.hub_pty = None
        self.hub_job = None
        self.returncode = None
        self._code = 1
        self._stdout, self._stderr = stdout, stderr
        self._out = self._err = b""
        self._done = threading.Event()
        want = stdout is not subprocess.DEVNULL and stdout is not None
        script, self._files = job_script(args, env, want)
        threading.Thread(target=self._run, args=(script,), daemon=True, name="termux-job").start()

    def _run(self, script):
        try:
            self._finish(*transport(script, JOB_TIMEOUT))
        except Exception as e:  # the hub's callers must always see an end
            self._err = _red("Termux could not be asked: %s" % e)
            self._code = 1
        finally:
            try:
                for target, data in ((self._stdout, self._out), (self._stderr, self._err)):
                    name = getattr(target, "name", None)
                    if isinstance(name, str) and data:  # the caller may have closed its handle already
                        with open(name, "ab") as f:
                            f.write(data)
            finally:
                # Only now "ended": callers read the files the moment poll() says so.
                self.returncode = self._code
                self._done.set()

    def _finish(self, rc, out, err, msg):
        code, files, missing = parse_job(out)
        for local, name in self._files:
            if name in files:
                tmp = local + ".termux"
                Path(tmp).write_bytes(files[name])
                os.replace(tmp, local)
        self._out = files.get("@stdout", b"")
        self._err = files.get("@stderr", b"")
        problem = NO_ANICLI if missing else termux_error(msg, err) if code is None else ""
        if problem:
            self._err += _red(problem)
        elif code is None:
            self._err += _red("Termux ended the job: %s" % ((msg or err).strip() or "no reason given"))
        self._code = code if code is not None else 1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("ani-cli", timeout)
        return self.returncode

    def communicate(self, input=None, timeout=None):
        self.wait(timeout)
        return (self._out if self._stdout == subprocess.PIPE else None,
                self._err if self._stderr == subprocess.PIPE else None)

    def send_signal(self, sig):
        pass

    def terminate(self):
        pass

    def kill(self):
        pass


# ---- the VPN check ----
# Android hides /sys/class/net from apps, so the Linux check can't tell
# (known False: no warning at all). Android names the VPN itself.

def _java_vpn():
    from java import jclass
    return str(jclass("io.github.elias8832.animehub.NetCheck").vpn())


vpn_probe = _java_vpn  # tests swap it


def vpn_status():
    """settings.vpn_status's answer on Android: a VPN carrying the phone's
    internet traffic (a mesh VPN like Tailscale without an exit node doesn't
    count, as on the PC)."""
    try:
        name = vpn_probe()
    except Exception:
        return {"active": False, "iface": None, "known": False}
    return {"active": bool(name), "iface": name or None, "known": True}


# ---- your own files ----
# Listing the storage's top folders works without any permission, so only
# Android itself can say whether the app may read the files in them.

def _java_files():
    from java import jclass
    return bool(jclass("io.github.elias8832.animehub.NetCheck").files())


files_probe = _java_files  # tests swap it


def files_ok():
    """The app may read the phone's files (Android's "All files access")."""
    try:
        return bool(files_probe())
    except Exception:
        return False


# ---- ani-cli's script, read from Termux ----
# The hub checks ani-cli's text before it drives it (anicli_check). Termux's
# copy can't be read from here, so it is fetched and kept in the data folder.

def anicli_copy():
    return config.DATA / "termux-ani-cli"


_fetch_lock = threading.Lock()
_last = {"ts": 0.0, "problem": ""}


def fetch_anicli(force=False):
    """Bring Termux's ani-cli script over. True when a copy is here; a failure
    keeps what was there and remembers why (problem())."""
    with _fetch_lock:
        if not force and time.monotonic() - _last["ts"] < 20:
            return anicli_copy().is_file()
        _last["ts"] = time.monotonic()
        try:
            rc, out, err, msg = transport('command -v ani-cli >/dev/null || { echo "@@NOANICLI"; exit 0; }\n'
                                          'cat "$(command -v ani-cli)"', FETCH_TIMEOUT)
        except Exception as e:
            rc, out, err, msg = -1, "", "", str(e)
        if out.startswith("#!"):
            p = anicli_copy()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(out, encoding="utf-8")
            os.replace(tmp, p)
            _last["problem"] = ""
            return True
        _last["problem"] = NO_ANICLI if "@@NOANICLI" in out else termux_error(msg, err) or NO_TERMUX
        return anicli_copy().is_file()


def problem():
    """Why ani-cli can't be reached in Termux, in the page's words; "" if it can."""
    return _last["problem"]


def anicli_version():
    try:
        m = re.search(r'version_number="?([0-9][0-9.]*)', anicli_copy().read_text(encoding="utf-8"))
    except OSError:
        return None
    return m[1] if m else None
