@echo off
rem "Add to Start menu.cmd" in the Windows zip: puts an "Anime Hub" shortcut to
rem AnimeHub.exe (this folder) into the user's Start menu. Run it again after
rem moving the folder. To remove: right-click Anime Hub in the Start menu,
rem "Open file location", delete the shortcut.
set "ANIME_HUB_DIR=%~dp0"
powershell -NoProfile -Command "$d = $env:ANIME_HUB_DIR; $s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs') + '\Anime Hub.lnk'); $s.TargetPath = $d + 'AnimeHub.exe'; $s.WorkingDirectory = $d; $s.IconLocation = $d + 'AnimeHub.exe,0'; $s.Description = 'Your anime library, AniList sync and mpv playback'; $s.Save()" && (echo Anime Hub is in the Start menu now.) || (echo Could not add Anime Hub to the Start menu.)
pause
