"""Anime Hub: a local anime library server. Run it with hub.py."""

import time

# The released version this is. The release gate bumps it (PLAN.md): update.py
# compares it with GitHub's tags.
VERSION = "0.4"

# When the package started loading: main() reports a slow start (TODO 74).
LOADED_AT = time.monotonic()
