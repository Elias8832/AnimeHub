Anime Hub - experimental Windows build
======================================

Start: double-click AnimeHub.exe. Keep this folder together - AnimeHub.exe
needs python\ and app\ beside it. Closing the window quits the app, unless
you switch on "Keep running after closing" in Settings > Phone remote.

Start menu: Anime Hub adds itself the first time it starts, and again
after you move this folder. "Add to Start menu.cmd" does the same by hand.

Windows may warn "Windows protected your PC" the first time (the program
is not signed): click "More info", then "Run anyway".


What to install
---------------
Anime Hub brings its own Python. It plays everything in mpv, and online
episodes and downloads go through your own ani-cli, which runs in Git for
Windows' bash. The first-run tour has an "Install what's missing" button
(also in Settings > System > Tools): it installs Scoop (https://scoop.sh),
Git, mpv, ani-cli and yt-dlp into your own user folder, no admin rights
needed. What it printed is in %LOCALAPPDATA%\anime-hub\tools-install.log.

By hand instead, in PowerShell:

    Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
    scoop install git
    scoop bucket add extras
    scoop install extras/mpv ani-cli yt-dlp

The window is a Microsoft Edge app window (Edge comes with Windows); an
installed Chrome or Brave is used first.

Phone remote: the first time you switch it on, Windows Firewall asks about
python.exe. Allow it. If Windows treats your home network as "Public", allow
public networks too, or the phone can't reach the PC.


Where things are
----------------
Settings:    %APPDATA%\anime-hub\config.json
Data:        %LOCALAPPDATA%\anime-hub\  (database, posters, your AniList
             login - it stays in your own Windows profile)
Window/log:  %LOCALAPPDATA%\anime-hub-wrapper\  (hub.log = the hub's output,
             wrapper.log = what the app did at each start - send both
             along if Anime Hub doesn't start)

Without the app window, in this folder:

    python\python.exe app\hub.py

starts the hub and opens it in your browser.

This is an experimental build: please report what does not work.
