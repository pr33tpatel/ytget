#!/usr/bin/env python3
import click
import yt_dlp
import os
import json
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import print as rprint

console = Console()

DEFAULT_OUTPUT_DIR = str(Path.home() / "Music" / "YouTube")
ARCHIVE_FILE      = str(Path.home() / ".ytget_archive.txt")
MANIFEST_DIR      = Path.home() / ".ytget_manifests"


# ─── Manifest helpers ─────────────────────────────────────────────────────────

def get_manifest_path(playlist_id: str) -> Path:
    MANIFEST_DIR.mkdir(exist_ok=True)
    return MANIFEST_DIR / f"{playlist_id}.json"

def load_manifest(playlist_id: str) -> dict:
    p = get_manifest_path(playlist_id)
    if p.exists():
        return json.loads(p.read_text())
    return {"playlist_id": playlist_id, "playlist_title": "", "last_updated": None, "tracks": {}}

def save_manifest(manifest: dict):
    manifest["last_updated"] = datetime.now().isoformat()
    p = get_manifest_path(manifest["playlist_id"])
    p.write_text(json.dumps(manifest, indent=2))

def make_progress_hook(manifest: dict):
    """yt-dlp progress hook: records each finished download into the manifest."""
    def hook(d):
        if d["status"] == "finished":
            info = d.get("info_dict", {})
            vid_id = info.get("id")
            if vid_id:
                manifest["tracks"][vid_id] = {
                    "title":        info.get("title", "Unknown"),
                    "filename":     d.get("filename", ""),
                    "uploader":     info.get("uploader", ""),
                    "downloaded_at": datetime.now().isoformat(),
                }
                save_manifest(manifest)
    return hook

def extract_playlist_id(url: str) -> str | None:
    """Quickly fetch just the playlist ID without downloading."""
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True,
                            "playlistend": 1, "ignoreerrors": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("id") if info else None


# ─── Base options ─────────────────────────────────────────────────────────────

def get_base_opts(output_dir: str, archive: bool, manifest: dict | None = None) -> dict:
    opts = {
        "outtmpl":                       os.path.join(output_dir, "%(playlist_title)s/%(title)s.%(ext)s"),
        "ignoreerrors":                  True,
        "retries":                       5,
        "fragment_retries":              10,
        "concurrent_fragment_downloads": 4,
        "embedthumbnail":                True,
        "addmetadata":                   True,
    }
    if archive:
        opts["download_archive"] = ARCHIVE_FILE
    if manifest:
        opts["progress_hooks"] = [make_progress_hook(manifest)]
    return opts


# ─── CLI group ────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """ytget — YouTube downloader powered by yt-dlp"""
    pass


# ─── Format listing ───────────────────────────────────────────────────────────

@cli.command("formats")
@click.argument("url")
def list_formats(url):
    """List all available formats for a video."""
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    table = Table(title=f"Formats: {info.get('title', url)}")
    table.add_column("ID",         style="cyan")
    table.add_column("Ext",        style="green")
    table.add_column("Resolution", style="magenta")
    table.add_column("FPS",        style="yellow")
    table.add_column("Bitrate",    style="blue")
    table.add_column("Note",       style="white")

    for f in info.get("formats", []):
        table.add_row(
            str(f.get("format_id", "")),
            str(f.get("ext", "")),
            str(f.get("resolution", f.get("height", "audio only"))),
            str(f.get("fps", "")),
            str(f.get("tbr", "")),
            str(f.get("format_note", "")),
        )
    console.print(table)


# ─── Audio-only download ──────────────────────────────────────────────────────

@cli.command("audio")
@click.argument("url")
@click.option("--format", "-f", default="mp3",
              type=click.Choice(["mp3", "flac", "opus", "m4a", "wav"]),
              help="Audio codec (default: mp3)")
@click.option("--quality", "-q", default="0",
              help="Audio quality: 0=best VBR, 9=worst, or e.g. '320K'")
@click.option("--output",   "-o", default=DEFAULT_OUTPUT_DIR, help="Output directory")
@click.option("--no-archive",    is_flag=True, help="Skip download archive check")
@click.option("--sponsorblock",  is_flag=True, help="Remove sponsor/intro/outro segments")
@click.option("--playlist-start", default=1,    help="Start at playlist index")
@click.option("--playlist-end",   default=None, type=int, help="End at playlist index")
def download_audio(url, format, quality, output, no_archive, sponsorblock,
                   playlist_start, playlist_end):
    """Download audio only. Works for single videos and full playlists."""
    playlist_id = extract_playlist_id(url)
    manifest    = load_manifest(playlist_id) if playlist_id else None
    if manifest:
        manifest["playlist_title"] = manifest.get("playlist_title") or playlist_id

    opts = get_base_opts(output, not no_archive, manifest)
    opts.update({
        "format": "bestaudio/best",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": format, "preferredquality": quality},
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
        "playlist_items": f"{playlist_start}:{playlist_end}" if playlist_end else f"{playlist_start}:",
    })

    if sponsorblock:
        opts["postprocessors"] += [
            {"key": "SponsorBlock",    "categories": ["sponsor", "intro", "outro", "selfpromo"]},
            {"key": "ModifyChapters",  "remove_sponsor_segments": ["sponsor", "intro", "outro", "selfpromo"]},
        ]

    console.rule(f"[bold green]Downloading Audio → {format.upper()}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    if manifest:
        console.print(f"\n[dim]Manifest saved → {get_manifest_path(playlist_id)}[/dim]")


# ─── Video download ───────────────────────────────────────────────────────────

@cli.command("video")
@click.argument("url")
@click.option("--resolution", "-r", default="1080",
              type=click.Choice(["480", "720", "1080", "1440", "2160", "best"]),
              help="Max video resolution (default: 1080p)")
@click.option("--format-ext", "-e", default="mp4",
              type=click.Choice(["mp4", "mkv", "webm"]))
@click.option("--output",   "-o", default=DEFAULT_OUTPUT_DIR)
@click.option("--no-archive",    is_flag=True)
@click.option("--subs",          is_flag=True, help="Embed English subtitles")
@click.option("--playlist-start", default=1)
@click.option("--playlist-end",   default=None, type=int)
def download_video(url, resolution, format_ext, output, no_archive, subs,
                   playlist_start, playlist_end):
    """Download video + audio. Works for single videos and playlists."""
    playlist_id = extract_playlist_id(url)
    manifest    = load_manifest(playlist_id) if playlist_id else None

    fmt  = "bestvideo+bestaudio/best" if resolution == "best" \
           else f"bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]"

    opts = get_base_opts(output, not no_archive, manifest)
    opts.update({
        "format":               fmt,
        "merge_output_format":  format_ext,
        "postprocessors":       [{"key": "FFmpegMetadata"}, {"key": "EmbedThumbnail"}],
        "playlist_items":       f"{playlist_start}:{playlist_end}" if playlist_end else f"{playlist_start}:",
    })
    if subs:
        opts.update({"writesubtitles": True, "subtitleslangs": ["en"], "embedsubtitles": True})
        opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    console.rule(f"[bold cyan]Downloading Video → {resolution}p {format_ext.upper()}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    if manifest:
        console.print(f"\n[dim]Manifest saved → {get_manifest_path(playlist_id)}[/dim]")


# ─── Playlist info ────────────────────────────────────────────────────────────

@cli.command("info")
@click.argument("url")
def playlist_info(url):
    """Show playlist/video metadata without downloading."""
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = info.get("entries", [])
        console.print(f"\n[bold]Playlist:[/bold] {info.get('title')}")
        console.print(f"[bold]Count:[/bold] {len(entries)} videos\n")
        table = Table()
        table.add_column("#",        style="dim")
        table.add_column("Title",    style="white")
        table.add_column("Duration", style="green")
        for i, e in enumerate(entries or [], 1):
            if not e:
                continue
            dur     = e.get("duration")
            dur_str = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "?"
            table.add_row(str(i), e.get("title", "Unknown"), dur_str)
        console.print(table)
    else:
        console.print(f"\n[bold]Title:[/bold]    {info.get('title')}")
        console.print(f"[bold]Channel:[/bold]  {info.get('uploader')}")
        console.print(f"[bold]Duration:[/bold] {info.get('duration_string')}")
        console.print(f"[bold]Views:[/bold]    {info.get('view_count'):,}\n")


# ─── Playlist health check (removed video detection) ─────────────────────────

@cli.command("check")
@click.argument("url")
@click.option("--output", "-o", default=DEFAULT_OUTPUT_DIR, help="Your local music directory")
def check_playlist(url, output):
    """
    Diff a playlist against your local manifest.

    Reports:
      - NEW tracks (on YouTube, not yet downloaded)
      - REMOVED tracks (in your manifest, gone from YouTube — but check local file)
      - MISSING FILES (downloaded before, file deleted locally)
    """
    console.rule("[bold yellow]Fetching current playlist from YouTube...")

    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True,
                            "ignoreerrors": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        console.print("[red]Could not fetch playlist info.")
        return

    playlist_id    = info.get("id")
    playlist_title = info.get("title", playlist_id)
    manifest       = load_manifest(playlist_id)

    # IDs currently visible on YouTube (deleted/private ones won't appear here)
    current_ids = {
        e["id"] for e in (info.get("entries") or [])
        if e and e.get("id") and e.get("title") not in ("[Deleted video]", "[Private video]", None)
    }
    tracked_ids = set(manifest.get("tracks", {}).keys())

    new_tracks     = current_ids - tracked_ids           # on YT, not in manifest
    removed_ids    = tracked_ids - current_ids           # in manifest, gone from YT
    present_tracks = current_ids & tracked_ids           # both places

    # For each removed track, check if the local file still exists
    removed_with_file    = []
    removed_without_file = []
    for vid_id in removed_ids:
        track    = manifest["tracks"][vid_id]
        filepath = Path(track.get("filename", ""))
        if filepath.exists():
            removed_with_file.append((vid_id, track))
        else:
            removed_without_file.append((vid_id, track))

    # Check present tracks for missing local files
    missing_files = []
    for vid_id in present_tracks:
        track    = manifest["tracks"][vid_id]
        filepath = Path(track.get("filename", ""))
        if track.get("filename") and not filepath.exists():
            missing_files.append((vid_id, track))

    # ── Report ────────────────────────────────────────────────────────────────
    console.print(f"\n[bold]Playlist:[/bold] {playlist_title}  ([dim]{playlist_id}[/dim])")
    console.print(f"[bold]Last synced:[/bold] {manifest.get('last_updated', 'Never')}\n")

    # New tracks
    if new_tracks:
        console.print(f"[bold green]▲ {len(new_tracks)} new track(s) on YouTube (not yet downloaded):[/green bold]")
        for vid_id in new_tracks:
            entry = next((e for e in (info.get("entries") or []) if e and e.get("id") == vid_id), {})
            console.print(f"  [green]+[/green] {entry.get('title', vid_id)}  [dim](https://youtu.be/{vid_id})[/dim]")
    else:
        console.print("[green]✓ No new tracks — you're up to date.[/green]")

    console.print()

    # Removed but local file intact  ← the important one
    if removed_with_file:
        console.print(f"[bold yellow]⚠  {len(removed_with_file)} track(s) removed from YouTube — local copy preserved:[/yellow bold]")
        for vid_id, track in removed_with_file:
            console.print(f"  [yellow]![/yellow] {track['title']}")
            console.print(f"      [dim]File:       {track['filename']}[/dim]")
            console.print(f"      [dim]Downloaded: {track['downloaded_at'][:10]}[/dim]")
    
    # Removed and local file also gone
    if removed_without_file:
        console.print(f"\n[bold red]✗  {len(removed_without_file)} track(s) removed from YouTube AND local file missing:[/red bold]")
        for vid_id, track in removed_without_file:
            console.print(f"  [red]✗[/red] {track['title']}  [dim](downloaded {track['downloaded_at'][:10]})[/dim]")

    if not removed_with_file and not removed_without_file:
        console.print("[green]✓ No tracks removed from YouTube.[/green]")

    console.print()

    # Missing local files for otherwise healthy tracks
    if missing_files:
        console.print(f"[bold red]⚠  {len(missing_files)} track(s) exist on YouTube but local file is missing (re-download?):[/red bold]")
        for vid_id, track in missing_files:
            console.print(f"  [red]-[/red] {track['title']}  [dim](https://youtu.be/{vid_id})[/dim]")

    console.print(f"\n[dim]Manifest: {get_manifest_path(playlist_id)}[/dim]")


# ─── Archive management ───────────────────────────────────────────────────────

@cli.command("archive")
@click.option("--clear", is_flag=True, help="Clear the download archive")
@click.option("--show",  is_flag=True, help="Show archive stats")
def manage_archive(clear, show):
    """Manage the download archive (prevents re-downloading)."""
    archive_path = Path(ARCHIVE_FILE)
    if clear:
        if archive_path.exists():
            archive_path.unlink()
            console.print("[green]Archive cleared.[/green]")
        else:
            console.print("[yellow]No archive found.[/yellow]")
    elif show:
        if archive_path.exists():
            lines = archive_path.read_text().strip().splitlines()
            console.print(f"[bold]Archive:[/bold] {ARCHIVE_FILE}")
            console.print(f"[bold]Entries:[/bold] {len(lines)} videos tracked")
        else:
            console.print("[yellow]No archive file found yet.[/yellow]")


def main():
    cli()
