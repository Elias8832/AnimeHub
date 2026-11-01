# AnimeHub

Your anime in one place. Your files and your AniList list on one page per show, played in mpv.

## Important!
This app is early in development — expect bugs and breaking changes.

![Home](docs/screenshots/home.png)

## Why it's nice

- **One page per show.** Every season, movie and special together, with your AniList entry right there.
- **Your files and online episodes in one list.** Missing an episode? Watch or download it from the same page. Auto-play carries on from a file to an online episode.
- **Made for mpv.** Resume, skip openings and endings with Enter, marks on the timeline, next episode. Screenshots go to `Pictures/AnimeHub`. Your own `mpv.conf` still loads.
- **AniList stays in sync.** Progress and scores go back to your list, even after being offline.
- **A calendar of what's airing**, and a Journal of what you watched.
- **Your phone as a remote.** No app to install, and it has its own phone layout.

![Show](docs/screenshots/show.png)

![Library](docs/screenshots/library.png)

## Install

You need **Python 3.9+**(Linux), a **Chromium-family browser**(Linux and Windows) (chromium, chrome, brave or edge)

**From git** (needs rust/cargo)**:**

```sh
git clone https://github.com/Elias8832/AnimeHub.git
cd AnimeHub/desktop
cargo build --release
./install.sh
```

**AppImage:** download `AnimeHub-x86_64.AppImage` from the [Releases page](https://github.com/Elias8832/AnimeHub/releases), then:

```sh
chmod +x AnimeHub-x86_64.AppImage
./AnimeHub-x86_64.AppImage
```

<details>
<summary>Optional extras</summary>

An AniList account; ani-cli 5.1+ for online episodes (the app can fetch it, it needs curl); [Upscayl](https://upscayl.org/download) for sharper banners; ffprobe for faster episode lengths.

</details>

<details>
<summary>Windows (experimental)</summary>

New zips only come with bigger releases (v0.4, v0.5, …), so Windows can be a little behind. Python is inside the zip.

1. Download `AnimeHub-windows-x64.zip` from the [Releases page](https://github.com/Elias8832/AnimeHub/releases) and unzip it anywhere. Keep the folder together.
2. Install the tools in PowerShell ([Scoop](https://scoop.sh) and Git; ani-cli runs in Git's bash). Only local files? `scoop bucket add extras`, then `scoop install mpv`, is enough.

   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
   winget install Git.Git
   scoop bucket add extras
   scoop install ani-cli yt-dlp
   ```

3. Double-click `AnimeHub.exe`. If Windows warns you: "More info", then "Run anyway". `Add to Start menu.cmd` adds a menu entry.

Won't start? Logs are in `%LOCALAPPDATA%\anime-hub-wrapper\`.

</details>

<details>
<summary>Updating</summary>

`git pull`, then `cargo build --release && ./install.sh`. AppImage or Windows zip: download the new one (Windows: replace the whole folder). Your data is kept.

</details>

<details>
<summary>Library folders</summary>

One folder per show, with seasons as `Season 1` or `Part 1`, extras in `Specials` or `OVA`, and movies in `Movies`. Files can also sit directly in the show folder. Episode numbers come from names like `S01E05`, `E05`, `Episode 5` or `Title - 05`. Files are never renamed or moved.

</details>

<details>
<summary>Configuration</summary>

Most of it is in Settings. The file is `~/.config/anime-hub/config.json` (Windows: `%APPDATA%\anime-hub\config.json`), or use `--config FILE`. Every key is optional:

| Key | Meaning |
| --- | --- |
| `library_root`, `library_extra` | your folders (easiest from Settings → Library) |
| `port` | server port (default `8787`) |
| `data_dir` | where data lives (default `~/.local/share/anime-hub`, Windows: `%LOCALAPPDATA%\anime-hub`) |
| `window` | `"app"`, `"browser"` or `"none"` |
| `anilist_client_id` | your own AniList app id, only if you change `port` |
| `remote_port` | the phone remote's Wi-Fi port (default `port` + 1) |
| `keep_running` | the desktop app's server stays on after you close the window (default off; Settings → Phone remote) |
| `start_at_login` | the desktop app's server starts at login, without a window (default off; Settings → Phone remote) |
| `remote_hosts` | host names for reaching it through a proxy, e.g. `["pc.tailnet.ts.net"]` |

Your data is all in `data_dir`. Deleting that folder is a full reset.

</details>

## Privacy

No tracking, no accounts, no ads. Your data stays on your PC.

<details>
<summary>What it talks to, and security</summary>

- **Internet:** [AniList](https://anilist.co) (info, art, your list), [ani.zip](https://api.ani.zip) (episode titles and pictures), [AniSkip](https://aniskip.com) (only if you turn it on; it sees which episode you start and its length), GitHub (only when you get or update ani-cli). With online episodes, ani-cli contacts the streaming site; the app warns you when no VPN is on. Before you click, it contacts the site only while a VPN is up (Warn without VPN off lifts that): to link a show, refresh its episode list, or get the next episode ready.
- **Access:** the server only listens on your PC. The phone remote is off by default; when on, only phones you paired get in, and they can't change your folders or AniList account.
- **Proxy:** through `remote_hosts` there's no password, so everyone on that network can use it.
- **AniList login:** a token in `anilist_token.json`, readable only by your user.

</details>

## AI-notice

- AnimeHub was made with a lot of help from mainly GLM 5.3 Flash, Opus 5.5(after v0.2) and very little help from Sonnet 5.5.
- I want to make clear that using LLMs didn't take anyone's job here. AnimeHub simply wouldn't exist without them, because I would never have started this project.
- I **DID NOT** give a large LLM $200, a prompt and a couple of hours. I built this project step by step. This is my app, built with passion over many, many hours, and I want to keep working on it.

## Streaming/downloading/hosting/etc.-notice

- AnimeHub **contains no streaming, downloading or hosting of anime**, and no provider code.
- It plays files you already own and gets info and artwork from AniList.
- Online episodes and downloads are done by your own ani-cli. AnimeHub only starts it; it never contacts a video site itself.

## License

GPL-3.0. See [LICENSE](LICENSE).

## Credits

- Info, artwork, airing times and recommendations from [AniList](https://anilist.co). Unofficial; not affiliated with AniList.
- AnimeHub is a player for your files, not a list or tracking service. Your list lives on AniList.
- AniList data is kept on your PC only to show it, never as a backup. Free and non-commercial, following the [AniList API terms](https://docs.anilist.co/guide/terms-of-use).
- Playback by [mpv](https://mpv.io).
- Sharper banners by [Upscayl](https://upscayl.org), if you install it.
- Fonts: [Hanken Grotesk](https://github.com/marcologous/hanken-grotesk), [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) and [Barlow Condensed](https://github.com/jpt/barlow), all under the SIL Open Font License 1.1 (licence texts in `web/fonts/`). `mpv/fonts/` holds the same fonts as plain TTF files for the player's controls.
