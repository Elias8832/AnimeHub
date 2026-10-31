Anime Hub - experimental Windows build
======================================

Start: double-click AnimeHub.exe. Keep this folder together - AnimeHub.exe
needs python\ and app\ beside it. Closing the window quits the app.

Start menu: double-click "Add to Start menu.cmd" once (again after moving
this folder).

Windows may warn "Windows protected your PC" the first time (the program
is not signed): click "More info", then "Run anyway".


What to install
---------------
Anime Hub brings its own Python. It plays everything in mpv, and online
episodes and downloads go through your own ani-cli, which runs in Git for
Windows' bash. The easiest way is Scoop (https://scoop.sh). In PowerShell:

    winget install Git.Git
    scoop bucket add extras
    scoop install ani-cli yt-dlp

ani-cli brings mpv along. Only local files? Then mpv alone is enough:

    scoop bucket add extras
    scoop install mpv

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
