#!/bin/sh
# Puts this build on the rolling "unstable" release (Decision 76). The
# files go up first and the notes' build line last: the app reads that line
# (update.MARK), so it never sees a build number whose files aren't there.
#   .github/publish-unstable.sh <file>...
set -e
n="$GITHUB_RUN_NUMBER"
sha="$GITHUB_SHA"
when="$(TZ=UTC git show -s --date=format-local:%Y-%m-%dT%H:%M:%SZ --format=%cd "$sha")"
title="Unstable build $n"
notes="$(cat <<NOTES
The newest build of master, rebuilt after every change that passes the tests. New fixes land here first, and it can break.

- Linux: \`AnimeHub-x86_64.AppImage\`. In the app, Settings → System → Updates from → Unstable updates it with a click.
- Windows: \`AnimeHub-windows-x64.zip\`. In the app, the same switch updates it with a click too. Not tested on Windows between releases.

Commit: $sha

<!-- build $n $sha $when -->
NOTES
)"
git tag -f unstable "$sha"
git push -f origin refs/tags/unstable
if gh release view unstable >/dev/null 2>&1; then
  gh release upload unstable "$@" --clobber
  gh release edit unstable --prerelease --title "$title" --notes "$notes"
else
  gh release create unstable "$@" --prerelease --latest=false --title "$title" --notes "$notes"
fi
