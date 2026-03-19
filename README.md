# ytget

A personal YouTube downloader built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) with a clean Rich terminal UI, playlist registry, and local manifest tracking.

## Motivation

I built ytget because I believe everyone should have the right to own their media locally.

YouTube content disappears constantly — videos get hit with copyright claims, accounts get
terminated, or creators simply delete their work. Once it's gone, it's gone. If you've built
a playlist of music, live performances, freestyles, or anything else that matters to you,
you shouldn't have to hope YouTube keeps it available forever.

ytget is my answer to that. It's not just a downloader — it tracks what you've saved, tells
you when something gets removed from YouTube, and makes sure you always have a local copy of
the things you care about. Your media library should belong to you, not a platform.

## Features

- Download audio (mp3, flac, opus, m4a, wav) or video (mp4, mkv, webm)
- Playlist registry — use a playlist name instead of URL after first use
- Archive-based incremental downloads (only fetches new tracks)
- Local manifest tracking with auto-repair
- Rich progress bars with per-track download and postprocessing status
- SponsorBlock support for audio
- Optional thumbnail embedding

## Requirements

- Python 3.10+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/)
- [Node.js](https://nodejs.org/) (required by yt-dlp for full YouTube support)
- [pipx](https://pipx.pypa.io/) (recommended for installation)

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/ytget.git
cd ytget
pipx install .
```

## Usage

```bash
# First time — must use full URL to register the playlist
ytget audio "https://www.youtube.com/playlist?list=PLxxx"

# All subsequent runs — use the playlist name
ytget audio my_playlist
ytget video my_playlist -r 1080 -e mp4

# Single video
ytget audio "https://www.youtube.com/watch?v=xxxxx"
ytget video "https://www.youtube.com/watch?v=xxxxx" -r 720
```

## Commands

| Command | Description |
|---|---|
| `audio <target>` | Download audio only |
| `video <target>` | Download video + audio |
| `check <target>` | Inspect playlist against local manifest |
| `yt-unavailable <target>` | List deleted/private entries in playlist |
| `info <target>` | Show playlist/video metadata |
| `formats <target>` | List available formats |
| `repair <target>` | Repair stale manifest paths |
| `playlists` | List all registered playlists |
| `archive --show / --clear` | Manage download archive |

## Audio options

```
-f, --format     mp3 | flac | opus | m4a | wav  (default: mp3)
-q, --quality    0=best VBR, 9=worst, or e.g. 320K
--thumbnail      Embed YouTube thumbnail as cover art
--sponsorblock   Remove sponsor/intro/outro segments
--show-processing  Show FFmpeg postprocessing bars
--no-archive     Skip archive check (re-download)
--playlist-start / --playlist-end
```

## Video options

```
-r, --resolution  480 | 720 | 1080 | 1440 | 2160 | best  (default: 1080)
-e, --format-ext  mp4 | mkv | webm  (default: mp4)
--thumbnail       Embed YouTube thumbnail
--subs            Embed English subtitles
--show-processing Show FFmpeg postprocessing bars
--no-archive      Skip archive check
--playlist-start / --playlist-end
```

## Configuration

ytget stores its data in `~/.config/ytget/`:

| File | Purpose |
|---|---|
| `archive.txt` | yt-dlp download archive (prevents re-downloading) |
| `playlists.json` | Playlist name → ID registry |
| `manifests/<id>.json` | Per-playlist track manifest |

yt-dlp settings (e.g. JS runtime) are read from `~/.config/yt-dlp/config`.

## Disclaimer

ytget is provided as-is, without warranty of any kind, express or implied. By using this
software, you agree that the author is not responsible for any damages, legal consequences,
data loss, or harm to your hardware or software that may result from its use.

**This tool is intended for personal, private use only.** It is your responsibility to ensure
that any content you download complies with YouTube's Terms of Service, applicable copyright
law, and the laws of your jurisdiction. Downloading copyrighted content without the rights
holder's permission may be illegal in your country.

The author does not condone piracy, copyright infringement, or any illegal use of this
software. ytget was built with the belief that people should be able to preserve personal
media libraries, content they have a legitimate personal interest in, against the reality
that online platforms are not permanent archives.

Use this software responsibly and at your own risk.

## Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — the powerful downloader engine that makes
  this possible
- [FFmpeg](https://ffmpeg.org/) — audio/video processing and conversion
- [Rich](https://github.com/Textualize/rich) — beautiful terminal UI and progress bars
- [Click](https://click.palletsprojects.com/) — CLI framework
