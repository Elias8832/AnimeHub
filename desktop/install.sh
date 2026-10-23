#!/bin/sh
# Install the anime-hub desktop wrapper for the current user:
#   ~/.local/bin/anime-hub                       — the wrapper binary
#   ~/.local/share/icons/hicolor/.../anime-hub.png — app icon
#   ~/.local/share/applications/anime-hub.desktop  — launcher entry
#   ~/.config/anime-hub/hub.path                   — pointer to this repo's hub.py
#                                                    (lets the wrapper find the hub
#                                                    after the binary is copied out
#                                                    of the repo, e.g. on a laptop)
# After this, "Anime Hub" shows up in the app launcher / dock and closing
# the window quits the whole app (the hub child included — the wrapper
# keeps it on a kernel leash via PR_SET_PDEATHSIG).
#
# Run from the repo root:  desktop/install.sh
# AUR packaging later replaces this with proper file placement.
set -e
cd "$(dirname "$0")"

BIN="$HOME/.local/bin"
APP="$HOME/.local/share/applications"
ICON="$HOME/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$BIN" "$APP" "$ICON"
cp target/release/anime-hub-desktop "$BIN/anime-hub"
cp icons/256x256.png "$ICON/anime-hub.png"

# Write the hub-path pointer so the wrapper can find this repo's hub.py even
# after the binary is copied out of the repo (lookup order: hub.path file,
# walk up from the exe, hardcoded default).
REPO="$(cd .. && pwd)"
if [ ! -f "$REPO/hub.py" ]; then
  echo "warning: hub.py not found next to install.sh ($REPO); skipping hub.path" >&2
else
  mkdir -p "$HOME/.config/anime-hub"
  printf '%s\n' "$REPO/hub.py" > "$HOME/.config/anime-hub/hub.path"
fi

# The window's class, for docks and window rules: the wrapper runs native
# Wayland when the session has it, where Chromium names the window
# chrome-<host>__-<profile> (WAYLAND_CLASS in src/main.rs); on X11 it is
# --class=AnimeHub.
if [ -n "$WAYLAND_DISPLAY" ]; then WMCLASS="chrome-127.0.0.1__-Default"; else WMCLASS="AnimeHub"; fi

cat > "$APP/anime-hub.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Anime Hub
Comment=Your anime universe — local library, AniList sync, mpv playback
Exec=$BIN/anime-hub
TryExec=$BIN/anime-hub
Icon=$ICON/anime-hub.png
Categories=AudioVideo;Video;
StartupWMClass=$WMCLASS
EOF

chmod +x "$BIN/anime-hub" "$APP/anime-hub.desktop"
update-desktop-database "$APP" 2>/dev/null || true
echo "Installed: $BIN/anime-hub (+ launcher entry 'Anime Hub')"
