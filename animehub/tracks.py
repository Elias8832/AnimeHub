"""Audio and subtitle tracks remembered per show: the track you pick in mpv
is picked again for the show's next episode, a file or an online episode.
A track is remembered by its language and title (mpv's track numbers
differ from file to file); subtitles switched off stay off. A pick is kept
for the AniList entry and for the library show (player.track_shows). Only a
pick made while playing is remembered, never what mpv chose on its own."""

import json

from . import database, settings

KINDS = ("audio", "sub")


def _same(a, b):
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def selection(track_list):
    """{"audio": pick, "sub": pick} of what mpv plays now. A pick is
    {"lang", "title"}; subtitles switched off are {"off": True}; no audio
    track at all is None."""
    out = {"audio": None, "sub": {"off": True}}
    for t in track_list or ():
        if t.get("type") in KINDS and t.get("selected"):
            out[t["type"]] = {"lang": t.get("lang"), "title": t.get("title")}
    return out


def choose(track_list, kind, pick):
    """mpv's aid/sid value for a remembered pick: a track id, "no" for
    subtitles off, or None when this file has nothing that matches (mpv's
    own choice stays). The language must match when both name one; the
    title settles which of several tracks in that language."""
    if not pick:
        return None
    if pick.get("off"):
        return "no" if kind == "sub" else None
    best, score = None, 0
    for t in track_list or ():
        if t.get("type") != kind:
            continue
        lang, title = _same(t.get("lang"), pick.get("lang")), _same(t.get("title"), pick.get("title"))
        if pick.get("lang") and t.get("lang") and not lang:
            continue
        s = 2 * lang + title
        if s > score:
            best, score = t.get("id"), s
    return best


def changes(track_list, prefs):
    """{"aid": …, "sid": …} to set so mpv plays the remembered tracks;
    only what differs from what it plays now."""
    now = selection(track_list)
    out = {}
    for kind, prop in (("audio", "aid"), ("sub", "sid")):
        want = choose(track_list, kind, (prefs or {}).get(kind))
        if want is None:
            continue
        if want == "no":
            if not now["sub"].get("off"):
                out[prop] = "no"
        elif not any(t.get("id") == want and t.get("type") == kind and t.get("selected") for t in track_list):
            out[prop] = want
    return out


def enabled():
    return settings.automation_enabled("remember_tracks")


def load(shows):
    """The picks for the first of `shows` that remembers each kind."""
    out = {}
    with database.db() as c:
        for show in shows:
            r = c.execute("SELECT audio, sub FROM track_prefs WHERE show=?", (show,)).fetchone()
            for kind, v in zip(KINDS, r or ()):
                if v and kind not in out:
                    out[kind] = json.loads(v)
    return out


def save(shows, kind, pick):
    col = {"audio": "audio", "sub": "sub"}[kind]
    with database.db() as c:
        for show in shows:
            c.execute(f"""INSERT INTO track_prefs(show, {col}) VALUES(?,?)
                          ON CONFLICT(show) DO UPDATE SET {col}=excluded.{col}""",
                      (show, json.dumps(pick)))
