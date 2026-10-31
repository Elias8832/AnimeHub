"""Getting an online episode ready before the click (TODO 135, Decision 49).

ani-cli does its work, then starts the player. Here the player it starts is
a small script that waits for the click and only then runs the real mpv, so
ani-cli still starts mpv with its own referrer, subtitles and link, and the
hub never sees any of them (Decisions 1 and 12). The flags that depend on
the click (resume point, socket) reach mpv through the go file.

One episode at a time, and nothing here reaches the site unless
settings.site_ok() allows it, checked again at the click."""

import shutil
import subprocess
import threading
import time
import uuid

from . import anicli, settings, system
from .config import config


# ani-cli picks how to run its player by the path: it has to contain "mpv"
# and must not contain "flatpak" (see usable()).
WAIT_SH = r"""#!/bin/sh
# Written by Anime Hub (hub.py), regenerated on demand, edits are lost.
n=0
while [ ! -e "$HUB_ANICLI_GO" ]; do
    n=$((n + 1))
    [ "$n" -gt "$HUB_ANICLI_WAIT" ] && exit 0
    [ -e "$HUB_ANICLI_GO.stop" ] && exit 0
    [ -d "${HUB_ANICLI_GO%/*}" ] || exit 0
    sleep 0.1
done
while IFS= read -r flag; do
    set -- "$flag" "$@"
done <"$HUB_ANICLI_GO"
exec "$HUB_ANICLI_MPV" "$@"
"""
# A site's link doesn't last for ever, and the end of an episode can be a
# long way off from 80 %. A link that died anyway falls back to a normal start.
TTL = 15 * 60
PAGE_DELAY = 1.0  # opening a show and leaving at once prepares nothing

_lock = threading.Lock()
_slot = None
_want = None
_timer = None
_expiry = None
_swept = False  # folders a hub that quit left behind


def wait_script():
    """ani-cli's player setting for a prepared run, or None when ani-cli
    can't be given one (it splits this on whitespace)."""
    path = config.DATA / "anicli-mpv-wait.sh"
    script = WAIT_SH.encode()
    try:
        current = path.read_bytes()
    except OSError:
        current = None
    if current != script:
        config.DATA.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(script)
        tmp.chmod(0o755)
        system.replace(tmp, path)
    spelled = system.space_free_path(path)
    if not spelled or "flatpak" in spelled:
        return None
    return system.shell_path(spelled)


def end(slot):
    """End a prepared run nobody will use. The stop file comes first: on
    Windows a process that is just starting can slip past taskkill, and the
    wait script then ends by itself."""
    try:
        (slot["dir"] / "go.stop").touch()
    except OSError:
        pass
    anicli.stop_process_group(slot["proc"], system.SIGKILL)
    try:
        slot["proc"].wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    anicli.anicli_release(slot["proc"])
    clean(slot)


def clean(slot):
    """Remove a run's folder. Windows refuses while the ending run still
    holds its log open, so the rest happens in the background; never in the
    way of a click. A folder that outlasts this goes at the next start.
    A failed removal still takes the stop file with it, and a wait script
    that missed it would hold the log for good: it goes back every time."""
    folder = slot["dir"]

    def stay_stopped():
        try:
            (folder / "go.stop").touch()
        except OSError:
            pass

    def sweep():
        for _ in range(50):
            time.sleep(0.1)
            shutil.rmtree(folder, ignore_errors=True)
            if not folder.exists():
                return
            stay_stopped()

    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        stay_stopped()
        threading.Thread(target=sweep, daemon=True).start()


def discard():
    global _slot, _want, _timer, _expiry
    with _lock:
        slot, _slot, _want = _slot, None, None
        if _timer:
            _timer.cancel()
        if _expiry:
            _expiry.cancel()
        _timer = _expiry = None
    if slot:
        end(slot)


def _expire(slot):
    global _slot
    with _lock:
        if _slot is not slot:
            return
        _slot = None
    end(slot)


def _spawn(key, link, token, mode, sock, mpv_env):
    global _slot, _expiry, _swept
    exe, script = system.find_program("mpv"), wait_script()
    if not exe or not script:
        return
    if not _swept:
        _swept = True
        shutil.rmtree(config.DATA / "prepared", ignore_errors=True)
    folder = config.DATA / "prepared" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    env = anicli.anicli_env(link, ANI_CLI_PLAYER=script, ANI_CLI_MODE=mode, ANI_CLI_LOG="0",
                            HUB_ANICLI_GO=system.shell_path(folder / "go"), HUB_ANICLI_MPV=system.shell_path(exe),
                            HUB_ANICLI_WAIT=(TTL + 60) * 10, **mpv_env)
    err = folder / "stderr.log"
    try:
        with open(err, "ab") as f:
            proc = anicli.anicli_spawn(["--exit-after-play", "-e", token, link["query"]], env, subprocess.DEVNULL, f)
    except (OSError, anicli.AniCliError):
        shutil.rmtree(folder, ignore_errors=True)
        return
    slot = {"key": key, "proc": proc, "dir": folder, "err": err, "sock": sock, "ts": time.time()}
    _slot = slot
    _expiry = threading.Timer(TTL, _expire, (slot,))
    _expiry.daemon = True
    _expiry.start()


def _fire(key, args):
    global _slot, _want, _timer
    old = None
    with _lock:
        if _want != key:
            return
        _want = _timer = None
        if not settings.site_ok():
            return
        old, _slot = _slot, None
        if _expiry:
            _expiry.cancel()
        _spawn(key, *args)
    if old:
        end(old)


def request(key, link, token, mode, sock, mpv_env, delay=0.0):
    """Prepare this episode, in place of whatever was prepared. Asking for
    the same one again does nothing: pages are read often."""
    global _want, _timer
    with _lock:
        if key == _want or (_slot and _slot["key"] == key):
            return
        if _timer:
            _timer.cancel()
        _want = key
        _timer = threading.Timer(delay, _fire, (key, (link, token, mode, sock, mpv_env)))
        _timer.daemon = True
        _timer.start()


def take(key):
    """The click: the prepared run for this episode, or None. Anything else
    that is prepared ends here. A run that isn't done yet still counts: it
    is ahead of a new one, and mpv starts the moment it gets there."""
    global _slot, _want, _timer, _expiry
    with _lock:
        slot = _slot
        fits = (slot is not None and slot["key"] == key and slot["proc"].poll() is None
                and time.time() - slot["ts"] < TTL and settings.site_ok())
        if fits:
            _slot, _want = None, None
            if _timer:
                _timer.cancel()
            if _expiry:
                _expiry.cancel()
            _timer = _expiry = None
            return slot
    discard()
    return None


def go(slot, flags):
    """Let a prepared run start mpv with these flags. False when it can't."""
    tmp = slot["dir"] / "go.tmp"
    try:
        tmp.write_bytes("".join(f + "\n" for f in flags).encode())
        system.replace(tmp, slot["dir"] / "go")
    except OSError:
        return False
    return True
