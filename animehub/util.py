"""Small shared helpers: logging and timestamps."""

import sys
from datetime import datetime, timezone


CANONICAL_TS = "%Y-%m-%d %H:%M:%S"


def utc_stamp():
    """The one timestamp format the app writes: UTC 'YYYY-MM-DD HH:MM:SS' —
    SQLite's own datetime('now') form, so every string comparison and sort in
    SQL and Python agrees (READINESS B5)."""
    return datetime.now(timezone.utc).strftime(CANONICAL_TS)


def normalize_timestamp(value):
    """Map any historical timestamp representation onto the canonical UTC
    form. ISO strings (T separator, optional offset/Z/microseconds) convert;
    values that parse as nothing recognizable are kept verbatim — a migration
    must never silently drop data. Returns (normalized, changed)."""
    if not value:
        return value, False
    v = value.strip()
    try:
        return datetime.strptime(v, CANONICAL_TS).strftime(CANONICAL_TS), v != value
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return value, False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(CANONICAL_TS), True


def log(msg):
    print("anime-hub: %s" % msg, file=sys.stderr, flush=True)
