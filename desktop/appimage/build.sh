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

TOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$TOOL" ]; then
  TOOL="$OUT/tools/appimagetool-$ARCH.AppImage"
  if [ ! -x "$TOOL" ]; then
    mkdir -p "$OUT/tools"
    curl -fL -o "$TOOL" \
      "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$TOOL"
  fi
fi

VERSION="$(git describe --tags --always 2>/dev/null || echo dev)" \
  ARCH="$ARCH" "$TOOL" --no-appstream "$APPDIR" "$OUT/AnimeHub-$ARCH.AppImage"
echo "Built: $OUT/AnimeHub-$ARCH.AppImage"
