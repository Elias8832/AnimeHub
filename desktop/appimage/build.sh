#!/bin/sh
# Build AnimeHub-x86_64.AppImage into desktop/target/.
#   desktop/appimage/build.sh
# Needs cargo (for the wrapper). appimagetool comes from $APPIMAGETOOL, the
# PATH, or is downloaded once into desktop/target/tools/.
set -e
cd "$(dirname "$0")/../.."
REPO="$(pwd)"
OUT="$REPO/desktop/target"
APPDIR="$OUT/AppDir"
ARCH="$(uname -m)"

(cd desktop && cargo build --release)

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/anime-hub" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/applications"
cp desktop/target/release/anime-hub-desktop "$APPDIR/usr/bin/anime-hub"
cp -r hub.py animehub web mpv LICENSE "$APPDIR/usr/share/anime-hub/"
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +
cp desktop/appimage/AppRun "$APPDIR/AppRun"
cp desktop/icons/256x256.png "$APPDIR/anime-hub.png"
cp desktop/icons/256x256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/anime-hub.png"

# StartupWMClass: the wrapper runs native Wayland when the session has it,
# where Chromium names the window chrome-<host>__-<profile> (WAYLAND_CLASS
# in src/main.rs). A file inside the AppImage can't pick per session.
cat > "$APPDIR/anime-hub.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Anime Hub
Comment=Your anime universe — local library, AniList sync, mpv playback
Exec=anime-hub
Icon=anime-hub
Categories=AudioVideo;Video;
StartupWMClass=chrome-127.0.0.1__-Default
DESKTOP
cp "$APPDIR/anime-hub.desktop" "$APPDIR/usr/share/applications/"

# The download is a fixed release, checked against the hash pinned here (as
# build.ps1 does for Python): what builds the published AppImage can't change
# under us.
TOOL_VERSION="1.9.1"
case "$ARCH" in
  x86_64)  TOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0" ;;
  aarch64) TOOL_SHA256="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158" ;;
  *)       TOOL_SHA256="" ;;
esac
TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$TOOL" ]; then
  if [ -z "$TOOL_SHA256" ]; then
    echo "no pinned appimagetool for $ARCH: install one or set APPIMAGETOOL" >&2
    exit 1
  fi
  TOOL="$OUT/tools/appimagetool-$TOOL_VERSION-$ARCH.AppImage"
  if [ ! -f "$TOOL" ]; then
    mkdir -p "$OUT/tools"
    curl -fL -o "$TOOL.part" \
      "https://github.com/AppImage/appimagetool/releases/download/$TOOL_VERSION/appimagetool-$ARCH.AppImage"
    mv "$TOOL.part" "$TOOL"
  fi
  if [ "$(sha256sum "$TOOL" | cut -d' ' -f1)" != "$TOOL_SHA256" ]; then
    rm -f "$TOOL"
    echo "$TOOL does not match the pinned SHA-256 - removed, run again" >&2
    exit 1
  fi
  chmod +x "$TOOL"
fi

VERSION="$(git describe --tags --always 2>/dev/null || echo dev)" \
  ARCH="$ARCH" "$TOOL" --no-appstream "$APPDIR" "$OUT/AnimeHub-$ARCH.AppImage"
echo "Built: $OUT/AnimeHub-$ARCH.AppImage"
