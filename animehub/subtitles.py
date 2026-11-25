"""Custom subtitles: user-attached and mpv-dropped subtitle files copied into
<data>/subtitles/, keyed by episode slot so one subtitle works identically for
local files, online streams, and phone/Android playback (parity).

Slot keys:
  - "aid:<media_id>:<number>" for matched AniList entries (shared by local + online)
  - "ep:<episode_id>" for unmatched local folders / unmapped specials
"""

import base64
import binascii
import hashlib
import io
import os
import re
import secrets
import threading
import time
import zipfile
from pathlib import Path

from . import database, util
from .config import config

SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt")
MAX_SUB_BYTES = 8 * 1024 * 1024
MAX_BATCH_BYTES = 24 * 1024 * 1024
MAX_BATCH_FILES = 200
DELAY_MIN, DELAY_MAX = -60.0, 60.0

# Common trailing language tags in subtitle filenames ("Episode 01.de.ass", "01.eng.srt")
_LANG_MAP = {
    "en": ("en", "English"), "eng": ("en", "English"), "english": ("en", "English"),
    "de": ("de", "German"), "deu": ("de", "German"), "ger": ("de", "German"), "german": ("de", "German"), "deutsch": ("de", "German"),
    "ja": ("ja", "Japanese"), "jpn": ("ja", "Japanese"), "jp": ("ja", "Japanese"), "japanese": ("ja", "Japanese"),
    "fr": ("fr", "French"), "fra": ("fr", "French"), "fre": ("fr", "French"), "french": ("fr", "French"),
    "es": ("es", "Spanish"), "spa": ("es", "Spanish"), "spanish": ("es", "Spanish"),
    "pt": ("pt", "Portuguese"), "por": ("pt", "Portuguese"), "pt-br": ("pt", "Portuguese (BR)"), "ptbr": ("pt", "Portuguese (BR)"),
    "it": ("it", "Italian"), "ita": ("it", "Italian"), "italian": ("it", "Italian"),
    "ru": ("ru", "Russian"), "rus": ("ru", "Russian"), "russian": ("ru", "Russian"),
    "pl": ("pl", "Polish"), "pol": ("pl", "Polish"), "polish": ("pl", "Polish"),
    "nl": ("nl", "Dutch"), "nld": ("nl", "Dutch"), "dut": ("nl", "Dutch"), "dutch": ("nl", "Dutch"),
    "ko": ("ko", "Korean"), "kor": ("ko", "Korean"), "korean": ("ko", "Korean"),
    "zh": ("zh", "Chinese"), "zho": ("zh", "Chinese"), "chi": ("zh", "Chinese"), "chs": ("zh", "Chinese"), "cht": ("zh", "Chinese"),
    "ar": ("ar", "Arabic"), "ara": ("ar", "Arabic"), "arabic": ("ar", "Arabic"),
    "tr": ("tr", "Turkish"), "tur": ("tr", "Turkish"), "turkish": ("tr", "Turkish"),
    "id": ("id", "Indonesian"), "ind": ("id", "Indonesian"),
    "vi": ("vi", "Vietnamese"), "vie": ("vi", "Vietnamese"),
    "th": ("th", "Thai"), "tha": ("th", "Thai"),
    "cs": ("cs", "Czech"), "ces": ("cs", "Czech"), "cze": ("cs", "Czech"),
    "hu": ("hu", "Hungarian"), "hun": ("hu", "Hungarian"),
    "uk": ("uk", "Ukrainian"), "ukr": ("uk", "Ukrainian"),
    "sv": ("sv", "Swedish"), "swe": ("sv", "Swedish"),
    "fi": ("fi", "Finnish"), "fin": ("fi", "Finnish"),
    "ro": ("ro", "Romanian"), "ron": ("ro", "Romanian"),
}

_SLOT_RE = re.compile(r"^(?:aid:\d+:[0-9]+(?:\.[0-9]+)?|ep:[A-Za-z0-9:_-]{1,120})\Z")
_LANG_CLEAN_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,4})?\Z", re.I)
_SRT_TIME = re.compile(r"(\d{1,2}:\d{2}:\d{2}),(\d{3})")
_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{1,3})$")
_ASS_TAG = re.compile(r"\{[^}]*\}")
_ASS_DRAW = re.compile(r"\{[^}]*\\p[1-9][^}]*\}")
_BARE_NUM_RE = re.compile(r"^(?:ep(?:isode)?\s*)?0*(\d{1,4})(?:v\d+)?$", re.I)
_END_NUM_RE = re.compile(r"(?:^|[\s._\-])0*(\d{1,4})(?:v\d+)?$")

# Short-lived in-memory stashes: batch preview tokens & undo tokens.
_lock = threading.Lock()
_staged = {}   # token -> {"ts": float, "items": [{name, ext, raw, label, lang, suggested_ep, size}]}
_undo = {}     # token -> {"ts": float, "rows": [(row_dict, bytes)]}
STAGE_TTL = 1800
UNDO_TTL = 600
MAX_STASH = 6


class SubtitleError(ValueError):
    pass


def sub_dir():
    return config.DATA / "subtitles"


def valid_slot(slot):
    return isinstance(slot, str) and bool(_SLOT_RE.match(slot))


def aid_slot(media_id, number):
    return f"aid:{int(media_id)}:{number}"


def ep_slot(ep_id):
    return f"ep:{ep_id}"


def slot_keys_for_episode(ep_id):
    """Slot keys for an episode: its AniList slot first (shared across disk
    and online), plus its raw ep id fallback for unmatched folders."""
    from . import player  # local import avoids import cycle
    keys = []
    if isinstance(ep_id, str) and ep_id:
        try:
            hit = player.entry_of_episode(ep_id)
        except Exception:
            hit = None
        if hit and hit[0] and hit[1] is not None:
            keys.append(aid_slot(hit[0], hit[1]))
        if not ep_id.startswith("stream:"):
            keys.append(ep_slot(ep_id))
        elif not keys:
            keys.append(ep_slot(ep_id))
    return keys


def primary_slot_for_episode(ep_id):
    keys = slot_keys_for_episode(ep_id)
    return keys[0] if keys else None


def _clean_label(text, fallback="Custom"):
    s = re.sub(r"\s+", " ", str(text or "").strip())
    return (s[:80] or fallback)


def _clean_lang(lang):
    if not isinstance(lang, str):
        return None
    s = lang.strip().lower()
    if s in _LANG_MAP:
        return _LANG_MAP[s][0]
    return s if _LANG_CLEAN_RE.match(s) else None


def guess_label_and_lang(filename):
    """Turn a subtitle filename into (label, lang). Strips extension and
    recognizes trailing language tags like '.de', '.eng', '.pt-br'."""
    name = Path(str(filename or "")).name
    stem = Path(name).stem
    lang = None
    lang_name = None
    parts = stem.split(".")
    if len(parts) >= 2:
        tail = parts[-1].strip().lower()
        if tail in _LANG_MAP:
            lang, lang_name = _LANG_MAP[tail]
            stem = ".".join(parts[:-1]).strip()
    if not lang:
        m = re.search(r"[\s_\-\[(]([a-z]{2,3}|english|german|deutsch|japanese|french|spanish|portuguese|italian|russian|polish|dutch|korean|chinese|arabic|turkish)[\])]$", stem, re.I)
        if m and m.group(1).lower() in _LANG_MAP:
            lang, lang_name = _LANG_MAP[m.group(1).lower()]
    cleaned = re.sub(r"[_]+", " ", stem).strip(" -.")
    if lang_name and (not cleaned or _BARE_NUM_RE.match(cleaned)):
        label = lang_name
    elif lang_name and lang_name.lower() not in cleaned.lower():
        label = f"{lang_name} · {cleaned}" if len(cleaned) <= 36 else lang_name
    else:
        label = cleaned or lang_name or "Custom"
    return _clean_label(label), lang


def decode_subtitle_bytes(raw):
    """Decode subtitle bytes (UTF-8/BOM, UTF-16, or CP1252 fallback) and
    return normalized UTF-8 text. Rejects empty or binary files."""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise SubtitleError("Subtitle file is empty.")
    if len(raw) > MAX_SUB_BYTES:
        raise SubtitleError("Subtitle file is too large (max 8 MB).")
    data = bytes(raw)
    text = None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as e:
            raise SubtitleError("Could not read this UTF-16 subtitle file.") from e
    else:
        if b"\x00" in data[:4096]:
            raise SubtitleError("That doesn't look like a text subtitle file.")
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if text is None or not text.strip():
        raise SubtitleError("Subtitle file has no readable text.")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _validate_ext(filename):
    ext = Path(str(filename or "")).suffix.lower()
    if ext not in SUB_EXTS:
        raise SubtitleError("Unsupported subtitle format — use .srt, .ass, .ssa or .vtt.")
    return ext


def _ass_time_to_vtt(ts):
    m = _ASS_TIME.match(ts.strip())
    if not m:
        return None
    h, mn, s, frac = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    ms = int(frac.ljust(3, "0")[:3])
    return f"{h:02d}:{mn:02d}:{s:02d}.{ms:03d}"


def ass_to_vtt(text):
    """Convert SubStation Alpha (.ass / .ssa) dialogue lines into plain WebVTT
    for HTML5 <track> on phones/Android. Keeps text & line breaks; drops ASS
    override tags and vector drawings."""
    cues = []
    in_events = False
    cols = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        low = line.lower()
        if low.startswith("format:"):
            cols = [c.strip().lower() for c in line.split(":", 1)[1].split(",")]
            continue
        if low.startswith("dialogue:"):
            if not cols:
                cols = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
            payload = line.split(":", 1)[1].lstrip()
            parts = payload.split(",", len(cols) - 1)
            if len(parts) < len(cols):
                continue
            row = dict(zip(cols, parts))
            start = _ass_time_to_vtt(row.get("start", ""))
            end = _ass_time_to_vtt(row.get("end", ""))
            body = row.get("text", "")
            if not start or not end or _ASS_DRAW.search(body):
                continue
            body = _ASS_TAG.sub("", body)
            body = body.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            body = "\n".join(x.strip() for x in body.splitlines() if x.strip())
            if body:
                cues.append((start, end, body))
    out = ["WEBVTT", ""]
    for start, end, body in cues:
        out.append(f"{start} --> {end}")
        out.append(body)
        out.append("")
    return "\n".join(out)


def to_vtt(text, ext=".srt"):
    """Convert .vtt, .srt, .ass or .ssa text into WebVTT for HTML5 <track>."""
    ext = (ext or ".srt").lower()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if ext in (".ass", ".ssa") or ("[Events]" in text and "Dialogue:" in text):
        return ass_to_vtt(text)
    if text.lstrip().startswith("WEBVTT"):
        return text
    # Plain SRT -> WebVTT: pad single-digit hours (0:01:02,000 -> 00:01:02.000)
    def fix_ts(m):
        hms, ms = m.group(1), m.group(2)
        h, mn, s = hms.split(":")
        return f"{int(h):02d}:{mn}:{s}.{ms}"
    return "WEBVTT\n\n" + _SRT_TIME.sub(fix_ts, text)


def _row_dict(r):
    slot = r[1]
    ep_num = None
    if slot.startswith("aid:"):
        tail = slot.split(":", 2)[2]
        try:
            ep_num = int(tail)
        except ValueError:
            try:
                ep_num = float(tail)
            except ValueError:
                ep_num = None
    p = sub_dir() / r[5]
    return {
        "id": r[0], "slot": slot, "ep_num": ep_num, "label": r[2], "lang": r[3],
        "ext": r[4], "file": r[5], "delay": float(r[6] or 0.0), "source": r[7],
        "created": r[8], "exists": p.is_file(),
    }


def _public(d):
    return {k: d[k] for k in ("id", "slot", "ep_num", "label", "lang", "ext", "delay", "source", "created")}


def get(sub_id):
    if not isinstance(sub_id, str) or not sub_id:
        return None
    with database.db() as c:
        r = c.execute(
            "SELECT id, slot, label, lang, ext, file, delay, source, created FROM custom_subtitles WHERE id=?",
            (sub_id,),
        ).fetchone()
    return _row_dict(r) if r else None


def file_path(sub_id):
    row = get(sub_id)
    if not row:
        return None
    p = sub_dir() / row["file"]
    return p if p.is_file() else None


def vtt_text(sub_id):
    row = get(sub_id)
    if not row:
        return None
    p = sub_dir() / row["file"]
    if not p.is_file():
        return None
    text = p.read_bytes()[:MAX_SUB_BYTES].decode("utf-8-sig", "replace")
    return to_vtt(text, row["ext"])


def list_for_slots(slots):
    """All custom subtitles attached to any of `slots`, oldest first (only
    rows whose file still exists on disk)."""
    slots = [s for s in dict.fromkeys(slots) if valid_slot(s)]
    if not slots:
        return []
    marks = ",".join("?" for _ in slots)
    with database.db() as c:
        rows = c.execute(
            f"SELECT id, slot, label, lang, ext, file, delay, source, created "
            f"FROM custom_subtitles WHERE slot IN ({marks}) ORDER BY created ASC, id ASC",
            slots,
        ).fetchall()
    return [_public(d) for r in rows if (d := _row_dict(r))["exists"]]


def for_episode(ep_id):
    """Full records (including disk `path`) for an episode, ready for mpv/phone."""
    keys = slot_keys_for_episode(ep_id)
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    with database.db() as c:
        rows = c.execute(
            f"SELECT id, slot, label, lang, ext, file, delay, source, created "
            f"FROM custom_subtitles WHERE slot IN ({marks}) ORDER BY created ASC, id ASC",
            keys,
        ).fetchall()
    out = []
    for r in rows:
        d = _row_dict(r)
        if d["exists"]:
            d["path"] = sub_dir() / d["file"]
            out.append(d)
    return out


def list_for_entry(media_id):
    """All custom subtitles across an AniList entry (`aid:<mid>:*`), ordered by
    episode number then creation time."""
    if not isinstance(media_id, int) or media_id <= 0:
        return []
    prefix = f"aid:{media_id}:%"
    with database.db() as c:
        rows = c.execute(
            "SELECT id, slot, label, lang, ext, file, delay, source, created "
            "FROM custom_subtitles WHERE slot LIKE ? ORDER BY created ASC, id ASC",
            (prefix,),
        ).fetchall()
    items = [_public(d) for r in rows if (d := _row_dict(r))["exists"]]
    items.sort(key=lambda x: (x["ep_num"] if isinstance(x["ep_num"], (int, float)) else 1e9, x["created"]))
    return items


def summary_for_entries(media_ids):
    """{media_id: {ep_num_str: [public_sub, ...]}} for pages.franchise_view."""
    ids = [int(i) for i in media_ids if isinstance(i, int) and i > 0]
    if not ids:
        return {}
    out = {}
    with database.db() as c:
        for mid in ids:
            rows = c.execute(
                "SELECT id, slot, label, lang, ext, file, delay, source, created "
                "FROM custom_subtitles WHERE slot LIKE ? ORDER BY created ASC, id ASC",
                (f"aid:{mid}:%",),
            ).fetchall()
            for r in rows:
                d = _row_dict(r)
                if not d["exists"]:
                    continue
                tail = d["slot"].split(":", 2)[2]
                out.setdefault(mid, {}).setdefault(tail, []).append(_public(d))
    return out


def summary_for_ep_ids(ep_ids):
    """{ep_id: [public_sub, ...]} for unmatched folder episodes (`ep:<id>`)."""
    ids = [i for i in ep_ids if isinstance(i, str) and i]
    if not ids:
        return {}
    slots = [ep_slot(i) for i in ids]
    out = {}
    with database.db() as c:
        for i in range(0, len(slots), 400):
            chunk = slots[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            for r in c.execute(
                f"SELECT id, slot, label, lang, ext, file, delay, source, created "
                f"FROM custom_subtitles WHERE slot IN ({marks}) ORDER BY created ASC, id ASC",
                chunk,
            ).fetchall():
                d = _row_dict(r)
                if d["exists"]:
                    out.setdefault(d["slot"][3:], []).append(_public(d))
    return out


def _clamp_delay(val):
    if isinstance(val, bool):
        raise SubtitleError("Delay must be a number.")
    try:
        x = float(val)
    except (TypeError, ValueError):
        raise SubtitleError("Delay must be a number.")
    if not (x == x and abs(x) != float("inf")):
        raise SubtitleError("Delay must be a finite number.")
    return round(max(DELAY_MIN, min(DELAY_MAX, x)), 2)


def add_subtitle(slot, filename, raw_bytes, label=None, lang=None, source="upload", delay=0.0):
    """Validate, normalize to UTF-8, store in <data>/subtitles/, and record in
    the database. Deduplicates identical content on the same slot."""
    if not valid_slot(slot):
        raise SubtitleError("Invalid episode slot.")
    ext = _validate_ext(filename)
    text = decode_subtitle_bytes(raw_bytes)
    utf8 = text.encode("utf-8")
    digest = hashlib.sha256(utf8).hexdigest()
    g_label, g_lang = guess_label_and_lang(filename)
    final_label = _clean_label(label, g_label) if label else g_label
    final_lang = _clean_lang(lang) if lang is not None else g_lang
    final_delay = _clamp_delay(delay)
    with database.db() as c:
        existing = c.execute(
            "SELECT id, slot, label, lang, ext, file, delay, source, created "
            "FROM custom_subtitles WHERE slot=? AND sha256=?",
            (slot, digest),
        ).fetchone()
        if existing:
            d = _row_dict(existing)
            if d["exists"]:
                if label and _clean_label(label) != d["label"]:
                    c.execute("UPDATE custom_subtitles SET label=? WHERE id=?", (final_label, d["id"]))
                    d["label"] = final_label
                pub = _public(d)
                pub["deduped"] = True
                return pub
            # Row existed but file was missing on disk: drop stale row and re-save.
            c.execute("DELETE FROM custom_subtitles WHERE id=?", (d["id"],))
        sub_id = secrets.token_hex(8)
        fname = f"{sub_id}{ext}"
        folder = sub_dir()
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / fname
        tmp = folder / f".{fname}.tmp"
        tmp.write_bytes(utf8)
        os.replace(tmp, dest)
        now = util.utc_stamp()
        c.execute(
            "INSERT INTO custom_subtitles(id, slot, label, lang, ext, file, sha256, delay, source, created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sub_id, slot, final_label, final_lang, ext, fname, digest, final_delay, source, now),
        )
    return _public({
        "id": sub_id, "slot": slot, "ep_num": _row_dict((sub_id, slot, final_label, final_lang, ext, fname, final_delay, source, now))["ep_num"],
        "label": final_label, "lang": final_lang, "ext": ext, "delay": final_delay, "source": source, "created": now,
    })


def import_base64(slot, name, data_b64, label=None, lang=None, source="upload"):
    if not isinstance(name, str) or not name.strip():
        raise SubtitleError("Missing subtitle filename.")
    if not isinstance(data_b64, str) or not data_b64:
        raise SubtitleError("Missing subtitle file data.")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise SubtitleError("Invalid file upload data.") from e
    return add_subtitle(slot, name, raw, label=label, lang=lang, source=source)


def update_subtitle(sub_id, label=None, delay=None):
    row = get(sub_id)
    if not row:
        raise SubtitleError("Subtitle not found.")
    sets, vals = [], []
    if label is not None:
        clean = _clean_label(label, "")
        if not clean:
            raise SubtitleError("Label cannot be empty.")
        sets.append("label=?")
        vals.append(clean)
    if delay is not None:
        sets.append("delay=?")
        vals.append(_clamp_delay(delay))
    if sets:
        vals.append(sub_id)
        with database.db() as c:
            c.execute(f"UPDATE custom_subtitles SET {', '.join(sets)} WHERE id=?", vals)
    return _public(get(sub_id))


def set_delay(sub_id, delay):
    """Best-effort delay save from mpv's poll loop."""
    if not sub_id:
        return
    val = _clamp_delay(delay)
    with database.db() as c:
        c.execute("UPDATE custom_subtitles SET delay=? WHERE id=?", (val, sub_id))


def _prune_stashes():
    now = time.time()
    for store, ttl in ((_staged, STAGE_TTL), (_undo, UNDO_TTL)):
        for k in [k for k, v in store.items() if now - v["ts"] > ttl]:
            store.pop(k, None)
        while len(store) > MAX_STASH:
            oldest = min(store, key=lambda k: store[k]["ts"])
            store.pop(oldest, None)


def _stash_undo(entries):
    """Save deleted rows+bytes in memory so Remove / Remove all has instant Undo."""
    if not entries:
        return None
    tok = secrets.token_urlsafe(12)
    with _lock:
        _prune_stashes()
        _undo[tok] = {"ts": time.time(), "entries": entries}
    return tok


def remove_ids(sub_ids):
    """Remove one or more custom subtitles by id. Returns (removed_records, undo_token)."""
    ids = [i for i in dict.fromkeys(sub_ids) if isinstance(i, str) and i]
    if not ids:
        return [], None
    marks = ",".join("?" for _ in ids)
    stash = []
    removed = []
    with database.db() as c:
        rows = c.execute(
            f"SELECT id, slot, label, lang, ext, file, sha256, delay, source, created "
            f"FROM custom_subtitles WHERE id IN ({marks})",
            ids,
        ).fetchall()
        for r in rows:
            p = sub_dir() / r[5]
            raw = p.read_bytes() if p.is_file() else b""
            stash.append((r, raw))
            removed.append({"id": r[0], "slot": r[1], "file": r[5], "path": p, "label": r[2]})
            p.unlink(missing_ok=True)
        if rows:
            c.execute(f"DELETE FROM custom_subtitles WHERE id IN ({marks})", ids)
    return removed, _stash_undo(stash)


def remove_for_entry(media_id):
    items = list_for_entry(media_id)
    return remove_ids([x["id"] for x in items])


def remove_for_slots(slots):
    items = list_for_slots(slots)
    return remove_ids([x["id"] for x in items])


def undo_remove(token):
    if not isinstance(token, str) or not token:
        raise SubtitleError("Nothing to undo.")
    with _lock:
        _prune_stashes()
        box = _undo.pop(token, None)
    if not box:
        raise SubtitleError("Undo expired.")
    folder = sub_dir()
    folder.mkdir(parents=True, exist_ok=True)
    restored = []
    with database.db() as c:
        for r, raw in box["entries"]:
            sub_id, slot, label, lang, ext, fname, sha256, delay, source, created = r
            if raw:
                dest = folder / fname
                tmp = folder / f".{fname}.tmp"
                tmp.write_bytes(raw)
                os.replace(tmp, dest)
            c.execute(
                "INSERT OR REPLACE INTO custom_subtitles(id, slot, label, lang, ext, file, sha256, delay, source, created) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                r,
            )
            restored.append(_public(_row_dict((sub_id, slot, label, lang, ext, fname, delay, source, created))))
    return restored


# --- Season batch import (multiple files or .zip) --------------------------

def guess_ep_number(filename):
    """Extract an episode number from a subtitle filename. Reuses the library
    scanner's parser first, then handles subtitle-only conventions like
    '01.de.ass' or 'Show_05.srt'."""
    from . import library
    name = Path(str(filename or "")).name
    n = library.parse_ep_number(name)
    if n is not None:
        return n
    stem = Path(name).stem
    # Strip trailing language tag(s): "01.en", "Show - 05.de"
    parts = stem.split(".")
    while len(parts) > 1 and parts[-1].strip().lower() in _LANG_MAP:
        parts.pop()
    stem = ".".join(parts).strip()
    n = library.parse_ep_number(stem + ".srt")
    if n is not None:
        return n
    bare = library.TAGS_RE.sub(" ", stem).strip()
    m = _BARE_NUM_RE.match(bare)
    if m:
        return int(m.group(1))
    m = _END_NUM_RE.search(bare)
    if m:
        num = int(m.group(1))
        if num < 1900:  # avoid matching release years like "2024"
            return num
    return None


def _extract_files(file_specs):
    """Unpack uploaded {name, data (base64)} entries — including .zip archives —
    into a flat list of (name, raw_bytes), with zip-bomb and size guards."""
    if not isinstance(file_specs, list) or not file_specs:
        raise SubtitleError("Pick at least one subtitle file or .zip archive.")
    out = []
    total_bytes = 0
    for spec in file_specs:
        if not isinstance(spec, dict):
            continue
        name = Path(str(spec.get("name") or "")).name
        data_b64 = spec.get("data")
        if not name or not isinstance(data_b64, str):
            continue
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise SubtitleError(f"Could not decode {name}.") from e
        ext = Path(name).suffix.lower()
        if ext == ".zip":
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile as e:
                raise SubtitleError(f"{name} is not a valid zip file.") from e
            with zf:
                for info in sorted(zf.infolist(), key=lambda x: x.filename.lower()):
                    if info.is_dir():
                        continue
                    parts = Path(info.filename).parts
                    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
                        continue
                    base = Path(info.filename).name
                    if Path(base).suffix.lower() not in SUB_EXTS:
                        continue
                    if len(out) >= MAX_BATCH_FILES:
                        raise SubtitleError(f"Too many subtitle files in one batch (max {MAX_BATCH_FILES}).")
                    if info.file_size > MAX_SUB_BYTES or total_bytes + info.file_size > MAX_BATCH_BYTES:
                        raise SubtitleError("Batch is too large (max 24 MB uncompressed).")
                    chunk = zf.read(info)
                    total_bytes += len(chunk)
                    out.append((base, chunk))
        elif ext in SUB_EXTS:
            if len(out) >= MAX_BATCH_FILES:
                raise SubtitleError(f"Too many subtitle files in one batch (max {MAX_BATCH_FILES}).")
            if len(raw) > MAX_SUB_BYTES or total_bytes + len(raw) > MAX_BATCH_BYTES:
                raise SubtitleError("Batch is too large (max 24 MB).")
            total_bytes += len(raw)
            out.append((name, raw))
    if not out:
        raise SubtitleError("No .srt, .ass, .ssa or .vtt files found.")
    return out


def stage_batch(file_specs, valid_numbers=None):
    """Unpack files (and any .zip), validate each subtitle, and auto-match to
    episode numbers. Returns {token, items} for preview before commit."""
    extracted = _extract_files(file_specs)
    valid_set = set(valid_numbers) if valid_numbers else None
    staged_items = []
    preview = []
    for idx, (name, raw) in enumerate(extracted):
        ext = Path(name).suffix.lower()
        label, lang = guess_label_and_lang(name)
        ep = guess_ep_number(name)
        if valid_set is not None and ep not in valid_set:
            ep = None
        err = None
        try:
            decode_subtitle_bytes(raw)
        except SubtitleError as e:
            err = str(e)
        staged_items.append({"idx": idx, "name": name, "ext": ext, "raw": raw, "label": label, "lang": lang, "error": err})
        preview.append({
            "idx": idx, "name": name, "ext": ext, "label": label, "lang": lang,
            "suggested_ep": ep, "size": len(raw), "error": err,
        })
    tok = secrets.token_urlsafe(12)
    with _lock:
        _prune_stashes()
        _staged[tok] = {"ts": time.time(), "items": staged_items}
    return {"ok": True, "token": tok, "items": preview}


def commit_batch(token, picks):
    """Commit chosen rows from a staged batch: picks = [{idx, slot, label?}]."""
    if not isinstance(token, str) or not token:
        raise SubtitleError("Missing batch token.")
    if not isinstance(picks, list) or not picks:
        raise SubtitleError("Select at least one matched episode to add.")
    with _lock:
        _prune_stashes()
        box = _staged.get(token)
    if not box:
        raise SubtitleError("Batch preview expired — drop the files again.")
    by_idx = {x["idx"]: x for x in box["items"]}
    added = []
    for p in picks:
        if not isinstance(p, dict):
            continue
        item = by_idx.get(p.get("idx"))
        slot = p.get("slot")
        if not item or item.get("error") or not valid_slot(slot):
            continue
        label = p.get("label") or item["label"]
        added.append(add_subtitle(slot, item["name"], item["raw"], label=label, lang=item["lang"], source="batch"))
    with _lock:
        _staged.pop(token, None)
    if not added:
        raise SubtitleError("No valid subtitles were selected.")
    return added


# --- mpv drag-and-drop auto-capture ----------------------------------------

def _norm_path(p):
    return os.path.normcase(os.path.normpath(str(p)))


def capture_mpv_external(ep_id, external_path, title=None, lang=None, video_path=None):
    """Auto-save a subtitle file the user dragged into mpv. Ignores URLs,
    files already managed in <data>/subtitles/, and sidecar subtitles sitting
    right next to a local video file (which mpv auto-loads by itself)."""
    if not isinstance(external_path, str) or not external_path.strip():
        return None
    if re.match(r"^(?:https?|ftp|memory|edl)://", external_path.strip(), re.I):
        return None
    p = Path(external_path)
    if p.suffix.lower() not in SUB_EXTS:
        return None
    try:
        resolved = p.resolve()
        managed = sub_dir().resolve()
        if resolved == managed or managed in resolved.parents:
            return None
    except OSError:
        return None
    if video_path is not None:
        try:
            vp = Path(video_path).resolve()
            if resolved.parent == vp.parent:
                stem = vp.stem.lower()
                sub_stem = resolved.stem.lower()
                if sub_stem == stem or sub_stem.startswith(stem + "."):
                    return None  # sidecar beside the video: already auto-loaded by mpv
        except OSError:
            pass
    if not resolved.is_file():
        return None
    try:
        if resolved.stat().st_size > MAX_SUB_BYTES:
            return None
        raw = resolved.read_bytes()
    except OSError:
        return None
    slot = primary_slot_for_episode(ep_id)
    if not slot:
        return None
    # Use filename for guessing unless mpv gave a nicer title different from filename
    g_label, g_lang = guess_label_and_lang(resolved.name)
    use_label = title if (title and title.strip() and title.strip() != resolved.name) else g_label
    use_lang = _clean_lang(lang) or g_lang
    try:
        return add_subtitle(slot, resolved.name, raw, label=use_label, lang=use_lang, source="mpv")
    except SubtitleError:
        return None
