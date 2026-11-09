#!/bin/sh
# Stamps a CI build so the app knows which build it is (Decision 76):
# animehub/build.json = {"number": <run number>, "commit": <sha>}. Never
# committed (.gitignore); a copy without it is a release or a git copy.
set -e
printf '{"number": %s, "commit": "%s"}\n' "$GITHUB_RUN_NUMBER" "$GITHUB_SHA" > animehub/build.json
cat animehub/build.json
