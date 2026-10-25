"""The ani-cli download queue."""

import os
import re
import subprocess
import threading
from pathlib import Path

from . import anicli, database, library, metadata, streaming, system, util
from .config import config


# Downloads: one ani-cli -d at a time, into the library folder, so a
# finished episode simply becomes a file the next scan picks up.
_dl_cv = threading.Condition()
_downloads = []
_dl_state = {"seq": 0, "worker": None}


def stream_download_dir(link, scan):
    """Where a release's downloads go: the folder season its files are in
    (entry_files), else a folder named after the release."""
    if config.LIBRARY is None:
        raise anicli.AniCliError("Choose a library folder in Settings first — downloads are saved there.")
    if not config.LIBRARY.is_dir():
        raise anicli.AniCliError(f"The library folder {config.LIBRARY} is not accessible right now.")
    home = streaming.entry_files(scan, link["media_id"])[1]
    if home and home != config.LIBRARY:
        return home
    name = link["meta"].get("title") or link["title"] or link["query"]
    # Plain quotes, as people type folder names (AniList titles use ’ “ ”).
    name = name.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    name = re.sub(r"[/\x00]", " ", name)
    name = " ".join(name.split()).lstrip(".") or f"anilist-{link['media_id']}"
    return config.LIBRARY / name


def downloads_payload():
    with _dl_cv:
        return [{k: v for k, v in j.items() if k not in ("proc", "cancel")} for j in _downloads]


def download_enqueue(media_id, tokens):
    try:
        link = streaming.stream_autolink(media_id)
    except anicli.AniCliError as e:
        return {"error": str(e)}
    scan = library.scan_library()
    library.merge_db(scan)
    try:
        dest = stream_download_dir(link, scan)
    except anicli.AniCliError as e:
        return {"error": str(e)}
    on_disk = set(streaming.entry_files(scan, media_id)[0])
    title = link["meta"].get("title") or link["title"] or link["query"]
    added = 0
    with _dl_cv:
        busy = {(j["media_id"], j["token"]) for j in _downloads if j["status"] in ("queued", "running")}
        for token in tokens:
            if token not in link["episodes"] or (media_id, token) in busy or streaming._stream_num(token) in on_disk:
                continue
            busy.add((media_id, token))
            _dl_state["seq"] += 1
            _downloads.append({"id": _dl_state["seq"], "media_id": media_id, "token": token,
                               "mode": link["mode"],
                               "show": title, "show_id": f"aid:{media_id}", "dest": str(dest),
                               "status": "queued", "percent": 0, "error": None})
            added += 1
        if added and _dl_state["worker"] is None:
            _dl_state["worker"] = threading.Thread(target=_dl_loop, daemon=True)
            _dl_state["worker"].start()
        _dl_cv.notify_all()
    return {"ok": True, "queued": added, "dest": str(dest)}


def download_cancel(job_id):
    with _dl_cv:
        job = next((j for j in _downloads if j["id"] == job_id), None)
        if not job:
            return {"error": "unknown download"}
        if job["status"] == "queued":
            job["status"] = "cancelled"
        elif job["status"] == "running":
            job["cancel"] = True
            if job.get("proc"):
                anicli.stop_process_group(job["proc"])
    return {"ok": True}


def download_clear():
    with _dl_cv:
        _downloads[:] = [j for j in _downloads if j["status"] in ("queued", "running")]
    return {"ok": True}


def _dl_loop():
    while True:
        with _dl_cv:
            job = None
            while job is None:
                job = next((j for j in _downloads if j["status"] == "queued"), None)
                if job is None:
                    _dl_cv.wait()
            job["status"] = "running"
        try:
            _dl_run(job)
        except Exception as e:
            util.log("download failed: %s" % e)
            with _dl_cv:
                job.pop("proc", None)
                job.update(status="failed", error=str(e))


def _dl_has_file(dest, token):
    try:
        return any(n.endswith(f" Episode {token}.mp4") for n in os.listdir(dest))
    except OSError:
        return False


def _dl_mark_dub(dest, token):
    """ani-cli names sub and dub downloads alike; a "(Dub)" suffix makes the
    dub its own version of the episode (scan reads "(...)" as a version
    label) and keeps a later sub download from overwriting it."""
    for n in os.listdir(dest):
        if n.endswith(f" Episode {token}.mp4"):
            target = dest / (n[:-4] + " (Dub).mp4")
            if not target.exists():
                os.replace(dest / n, target)
            return


def _dl_cleanup(dest, token, had_file, created):
    """Remove what an unfinished download left: yt-dlp's .part/.ytdl
    fragments, the subtitle, and a partial .mp4 (ffmpeg writes in place)."""
    stem = f" Episode {token}"
    try:
        names = os.listdir(dest)
    except OSError:
        return
    for n in names:
        if (stem + ".mp4.part" in n or n.endswith(stem + ".mp4.ytdl") or n.endswith(stem + ".vtt")
                or (not had_file and n.endswith(stem + ".mp4"))):
            try:
                (dest / n).unlink()
            except OSError:
                pass
    if created:
        try:
            dest.rmdir()  # only succeeds when nothing else is in it
        except OSError:
            pass


def _dl_run(job):
    link = streaming.stream_link_get(job["media_id"])
    if not link:
        raise anicli.AniCliError(streaming.STREAM_UNAVAILABLE)
    dest = Path(job["dest"])
    created = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    had_file = _dl_has_file(dest, job["token"])
    # ani-cli hands ANI_CLI_PLAYER_FLAGS to yt-dlp/ffmpeg when downloading;
    # the user's (mpv) flags would break them.
    mode = job.get("mode") or link["mode"]
    env = anicli.anicli_env(link, ANI_CLI_DOWNLOAD_DIR=system.shell_path(dest), ANI_CLI_PLAYER_FLAGS="", ANI_CLI_MODE=mode)
    proc = anicli.anicli_spawn(["-d", "-e", job["token"], link["query"]], env, subprocess.PIPE, subprocess.STDOUT)
    with _dl_cv:
        job["proc"] = proc
        if job.get("cancel"):
            anicli.stop_process_group(proc)
    tail, buf = [], b""
    try:
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            *lines, buf = re.split(rb"[\r\n]", buf + chunk)
            for raw in lines:
                line = raw.decode(errors="replace")
                m = anicli.DL_PERCENT_RE.search(line)
                if m:
                    size, eta = anicli.DL_SIZE_RE.search(line), anicli.DL_ETA_RE.search(line)
                    with _dl_cv:
                        job["percent"] = float(m.group(1))
                        # additive: "412 MB", "02:03" for the downloads list
                        if size:
                            job["size"] = "%d %sB" % (round(float(size.group(1))), size.group(2))
                        if eta:
                            job["eta"] = eta.group(1)
                elif line.strip():
                    tail = (tail + [line])[-40:]
        rc = proc.wait()
    finally:
        proc.stdout.close()
        anicli.anicli_release(proc)
    with _dl_cv:
        job.pop("proc", None)
        cancelled = job.get("cancel")
    if not cancelled and rc == 0 and _dl_has_file(dest, job["token"]):
        if mode == "dub" and not had_file:
            _dl_mark_dub(dest, job["token"])
        with _dl_cv:
            job.update(status="done", percent=100)
        _dl_adopt(job["media_id"], dest)
        return
    _dl_cleanup(dest, job["token"], had_file, created)
    with _dl_cv:
        if cancelled:
            job["status"] = "cancelled"
        else:
            msg = anicli.anicli_message("\n".join(tail + [buf.decode(errors="replace")]))
            util.log("download %s E%s failed: %s" % (job["media_id"], job["token"], msg or rc))
            job.update(status="failed", error=streaming.friendly_stream_error(msg, f"E{job['token']}"))


def _dl_adopt(media_id, dest):
    """A download that started a new folder becomes a new disk show — match
    it to the release right away instead of waiting for the matcher."""
    try:
        rel = str(dest.relative_to(config.LIBRARY))
        show = next((s for s in library.scan_library() if s["path"] == rel), None)
        if not show:
            return
        with database.db() as c:
            known = c.execute("SELECT 1 FROM shows_meta WHERE show_id=?", (show["id"],)).fetchone()
        if not known:
            metadata.set_match(show["id"], media_id)
    except Exception as e:
        util.log("matching downloaded folder failed: %s" % e)
