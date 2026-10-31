"""Staying up without the window (Decision 51): the desktop wrapper can keep
the hub alive after its window closes, and can start it at login with no
window, so the phone remote always finds it. The wrapper tells the hub where
it is through ANIME_HUB_WRAPPER; a hub started by hand has no wrapper, and
then these switches do nothing.

Linux gets two login entries, because no single one works everywhere: XDG
autostart (GNOME, KDE, Xfce) and a systemd user service (Hyprland and other
compositors that don't read autostart folders). Where both run, the second
wrapper finds the first one's lock and exits."""

import os
import shutil
import subprocess
from pathlib import Path

from . import system
from .config import CONFIG_PATH, _default_config_path

NAME = "anime-hub-server"  # not "anime-hub": a user may have their own unit by that name
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_VALUE = "Anime Hub"


def launcher():
    """The wrapper program this hub runs under (the AppImage, when it's one), or None."""
    path = os.environ.get("ANIME_HUB_WRAPPER")
    return path if path and os.path.isfile(path) else None


def _command():
    cmd = [launcher(), "--background"]
    if CONFIG_PATH != _default_config_path():
        cmd += ["--config", str(CONFIG_PATH)]
    return cmd


def _config_home():
    v = os.environ.get("XDG_CONFIG_HOME")
    return Path(v) if v and os.path.isabs(v) else Path.home() / ".config"


def _quoted(cmd):
    """One command line for an Exec= or ExecStart= field. Both read double
    quotes with backslashes and treat % as special."""
    def one(arg):
        for ch in ("\\", '"', "`", "$"):
            arg = arg.replace(ch, "\\" + ch)
        return '"%s"' % arg.replace("%", "%%")
    return " ".join(one(a) for a in cmd)


def _systemctl(*args):
    """Best effort: a system without systemd has only the autostart entry."""
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", *args], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=15, check=False)


def _linux_entries():
    line = _quoted(_command())
    return {
        _config_home() / "autostart" / f"{NAME}.desktop":
            f"[Desktop Entry]\nType=Application\nName=Anime Hub server\n"
            f"Comment=Keeps Anime Hub ready for the phone remote\nExec={line}\nTerminal=false\n",
        _config_home() / "systemd" / "user" / f"{NAME}.service":
            f"[Unit]\nDescription=Anime Hub server\nAfter=graphical-session.target\n"
            f"PartOf=graphical-session.target\n\n[Service]\nExecStart={line}\n\n"
            f"[Install]\nWantedBy=graphical-session.target\n",
    }


def _linux_add():
    changed = False
    for path, text in _linux_entries().items():
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed = True
    if changed:
        _systemctl("daemon-reload")
        _systemctl("enable", f"{NAME}.service")


def _linux_remove():
    _systemctl("disable", f"{NAME}.service")
    for path in (_config_home() / "autostart" / f"{NAME}.desktop",
                 _config_home() / "systemd" / "user" / f"{NAME}.service"):
        path.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _windows_add():
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, WINDOWS_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(_command()))


def _windows_remove():
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, WINDOWS_VALUE)
        except FileNotFoundError:
            pass


def set_login(on):
    """Add or remove the login entries. Raises OSError (or a subprocess error) when it can't."""
    if on:
        (_windows_add if system.WINDOWS else _linux_add)()
    else:
        (_windows_remove if system.WINDOWS else _linux_remove)()


def refresh():
    """At start: the app may have moved since the entry was written."""
    from .config import config
    if config.START_AT_LOGIN and launcher():
        try:
            set_login(True)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"anime-hub: could not update the start-at-login entry: {e}", flush=True)
