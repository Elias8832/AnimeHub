"""Getting an online episode ready before the click (TODO 135, Decision 49).

ani-cli does its work, then starts the player. Here the player it starts is
a small script that waits for the click and only then runs the real mpv, so
ani-cli still starts mpv with its own referrer, subtitles and link, and the
hub never sees any of them (Decisions 1 and 12). The flags that depend on
the click (resume point, socket) reach mpv through the go file.

One episode at a time, and nothing here reaches the site unless
settings.site_ok() allows it, checked again at the click.

Started ahead (TODO 214): a run made for the one slot also starts mpv the
moment ani-cli hands it the episode, paused and without a picture, reading
only a little ahead (ahead.lua). Opening the stream was most of the wait
after the click; the click now only shows the picture, over mpv's socket.
The player's Sub/Dub switch keeps the plain wait (spawn without `ahead`)."""

import json
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
: >"$HUB_ANICLI_GO.ready" 2>/dev/null
# Started ahead: mpv runs now with go.ahead's flags, paused and hidden, and the
# click shows it. "started" comes before the second look at the go file, so a
# click either finds it (and shows mpv) or left a go file this sees (below).
if [ -e "$HUB_ANICLI_GO.ahead" ] && [ ! -e "$HUB_ANICLI_GO" ]; then
    : >"$HUB_ANICLI_GO.started" 2>/dev/null
    if [ ! -e "$HUB_ANICLI_GO" ]; then
        while IFS= read -r flag; do
            set -- "$flag" "$@"
        done <"$HUB_ANICLI_GO.ahead"
        exec "$HUB_ANICLI_MPV" "$@"
    fi
fi
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
AHEAD_LUA = r"""-- Written by Anime Hub (hub.py), regenerated on demand, edits are lost.
-- A prepared run's mpv, started before the click (TODO 214): paused, and its
-- picture goes nowhere (vo=null) until the click, so the stream is open and
-- the start already read when the click shows it. Until then it reads only a
-- little ahead. The click ("animehub-ahead-show") gives the user's own video
-- output and reading back, then plays: picture and sound start together.
-- Nobody clicked: it ends by itself.
local utils = require "mp.utils"
local o = { dir = "", ttl = 960, ahead = 30 }
require("mp.options").read_options(o, "animehub-ahead")
local keep, done = {}, false
for _, k in ipairs({ "cache-secs", "demuxer-readahead-secs" }) do
    keep[k] = mp.get_property_native(k)
    mp.set_property_native(k, o.ahead)
end
local vo = mp.get_property_native("vo")
mp.set_property("vo", "null")
local born = mp.get_time()
local watch = mp.add_periodic_timer(2, function()
    if (o.dir ~= "" and (not utils.file_info(o.dir) or utils.file_info(o.dir .. "/go.stop")))
            or mp.get_time() - born > o.ttl then
        mp.command("quit")
    end
end)
local function show()
    if done then return end
    done = true
    watch:kill()
    mp.set_property_native("vo", vo)
    for k, v in pairs(keep) do mp.set_property_native(k, v) end
    mp.set_property_native("pause", false)
end
mp.register_script_message("animehub-ahead-show", show)
mp.observe_property("pause", "bool", function(_, paused)
    if paused == false then show() end -- unpaused some other way: the same
end)
"""

# A site's link doesn't last for ever, and the end of an episode can be a
# long way off from 80 %. A link that died anyway falls back to a normal start.
TTL = 15 * 60
PAGE_DELAY = 1.0  # opening a show and leaving at once prepares nothing
AHEAD = 30  # seconds of the episode mpv may read before the click (his mpv.conf reads all of it)
SHOW_WAIT = 10  # a click while mpv is still starting: how long the click waits for its socket

_lock = threading.Lock()
_slot = None
_want = None
_timer = None
_expiry = None
_swept = False  # folders a hub that quit left behind


def wait_script():
    """ani-cli's player setting for a prepared run, or None when ani-cli
    can't be given one (it splits this on whitespace)."""
    return player_script("anicli-mpv-wait.sh", WAIT_SH)


def hub_file(name, body):
    """A file the hub writes for its tools (written when it changed)."""
    path = config.DATA / name
    script = body.encode()
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
    return path


def player_script(name, body):
    """A hub script ani-cli runs as its player (written when it changed),
    as ani-cli's player setting; None when ani-cli can't be given it. The
    name must contain "mpv" (ani-cli's mpv branch passes the referrer)."""
    path = hub_file(name, body)
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
    if slot.get("ahead") is not None and (slot["dir"] / "go.started").exists():
        # An mpv that started ahead quits first: the user's own scripts end what
        # they started (thumbfast's helper player outlives a kill).
        try:
            _ipc(slot["sock"], ["quit"])
            slot["proc"].wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
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


def spawn(key, link, token, mode, sock, mpv_env, ahead=None):
    """Start a prepared run and hand it back, without taking the one slot:
    the player's Sub/Dub switch (TODO 153) gets the other language ready
    while the episode keeps playing. None when it can't be. `ahead`: the
    flags the click would give mpv now; mpv then starts as soon as it can,
    paused and hidden (TODO 214)."""
    global _swept
    exe, script = system.find_program("mpv"), wait_script()
    if not exe or not script:
        return None
    if not _swept:
        _swept = True
        shutil.rmtree(config.DATA / "prepared", ignore_errors=True)
    folder = config.DATA / "prepared" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    if ahead is not None:
        hidden = ["--pause", "--force-window=no", f"--script={hub_file('anicli-mpv-ahead.lua', AHEAD_LUA)}",
                  f"--script-opts-append=animehub-ahead-dir={folder}",
                  f"--script-opts-append=animehub-ahead-ttl={TTL + 60}",
                  f"--script-opts-append=animehub-ahead-ahead={AHEAD}"]
        (folder / "go.ahead").write_bytes("".join(f + "\n" for f in [*ahead, *hidden]).encode())
    env = anicli.anicli_env(link, ANI_CLI_PLAYER=script, ANI_CLI_MODE=mode, ANI_CLI_LOG="0",
                            HUB_ANICLI_GO=system.shell_path(folder / "go"), HUB_ANICLI_MPV=system.shell_path(exe),
                            HUB_ANICLI_WAIT=(TTL + 60) * 10, **mpv_env)
    err = folder / "stderr.log"
    try:
        with open(err, "ab") as f:
            proc = anicli.anicli_spawn(["--exit-after-play", "-e", token, link["query"]], env, subprocess.DEVNULL, f)
    except (OSError, anicli.AniCliError):
        shutil.rmtree(folder, ignore_errors=True)
        return None
    return {"key": key, "proc": proc, "dir": folder, "err": err, "sock": sock, "ts": time.time(),
            "ahead": None if ahead is None else list(ahead)}


def ready(slot):
    """ani-cli has the episode's link and waits for the go: its player
    script has started."""
    return (slot["dir"] / "go.ready").exists()


def _spawn(key, link, token, mode, sock, mpv_env, ahead=None):
    global _slot, _expiry
    slot = spawn(key, link, token, mode, sock, mpv_env, ahead)
    if not slot:
        return
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


def request(key, link, token, mode, sock, mpv_env, delay=0.0, ahead=None):
    """Prepare this episode, in place of whatever was prepared. Asking for
    the same one again does nothing: pages are read often. `ahead`: see
    spawn. Android has no mpv to prepare (Decision 88): the phone player
    asks when played."""
    global _want, _timer
    if system.ANDROID:
        return
    with _lock:
        if key == _want or (_slot and _slot["key"] == key):
            return
        if _timer:
            _timer.cancel()
        _want = key
        _timer = threading.Timer(delay, _fire, (key, (link, token, mode, sock, mpv_env, ahead)))
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
    """Let a prepared run start mpv with these flags. False when it can't
    (the caller ends it and starts the episode afresh). A run whose mpv
    started ahead is shown instead; it must have the click's flags but for
    where it starts."""
    ahead = slot.get("ahead")
    if ahead is not None and _but_start(flags) != _but_start(ahead):
        return False  # a setting changed since: mpv can't take it any more
    tmp = slot["dir"] / "go.tmp"
    try:
        tmp.write_bytes("".join(f + "\n" for f in flags).encode())
        system.replace(tmp, slot["dir"] / "go")
    except OSError:
        return False
    if ahead is None or not (slot["dir"] / "go.started").exists():
        return True  # mpv isn't running yet: the wait script finds the go file
    return show(slot, flags)


def show(slot, flags):
    """The click for an mpv that started ahead: where it starts, then
    AHEAD_LUA shows the picture and plays. Waits for its socket while mpv
    is still starting."""
    sock, end = slot["sock"], time.time() + SHOW_WAIT
    while True:
        try:
            if system.ipc_ready(sock):
                start = _start(flags)
                if start != _start(slot["ahead"]) and _ipc(sock, ["seek", start, "absolute"])[0]:
                    _ipc(sock, ["set_property", "start", "%.2f" % start])  # not open yet: it starts there
                _ipc(sock, ["script-message", "animehub-ahead-show"])
                _playing(sock)
                return True
        except OSError:
            pass
        if slot["proc"].poll() is not None or time.time() > end:
            return False
        time.sleep(0.02)


def _playing(sock, wait=1.5):
    """The script plays it; should it not have (its mpv runs no scripts), play here."""
    end = time.time() + wait
    while time.time() < end:
        if _ipc(sock, ["get_property", "pause"]) == (None, False):
            return
        time.sleep(0.05)
    _ipc(sock, ["set_property", "pause", False])


def _ipc(sock, command):
    """One command to mpv: (its error or None, its answer)."""
    buf = system.ipc_exchange(sock, (json.dumps({"command": command}) + "\n").encode(), 2.0)
    for line in buf.decode(errors="replace").splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "error" in msg:
            return (None if msg["error"] == "success" else msg["error"]), msg.get("data")
    return None, None


def _but_start(flags):
    return [f for f in flags if not f.startswith("--start=")]


def _start(flags):
    """--start in seconds (m:s or seconds), 0 without one."""
    for f in flags:
        if f.startswith("--start="):
            secs = 0.0
            for part in f.split("=", 1)[1].split(":"):
                secs = secs * 60 + float(part)
            return secs
    return 0.0
