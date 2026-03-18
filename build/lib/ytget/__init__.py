#!/usr/bin/env python3
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import click
import yt_dlp
from rich.console import Console
from rich.table import Table
from rich import print as rprint
from rich.progress import (
    Progress,
    BarColumn,
    TimeRemainingColumn,
    TextColumn,
    DownloadColumn,
    TransferSpeedColumn,
)

console = Console()

# -- Paths / config

CONFIG_DIR = Path.home() / ".config" / "ytget"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

MANIFEST_DIR = CONFIG_DIR / "manifests"
MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

ARCHIVE_FILE = str(CONFIG_DIR / "archive.txt")

# Windows-backed mount in Ubuntu VM
DEFAULT_OUTPUT_DIR = "/home/preet/YTMedia"


# -- Manifest helpers

def get_manifest_path(playlist_id: str) -> Path:
    return MANIFEST_DIR / f"{playlist_id}.json"


def load_manifest(playlist_id: str) -> Dict[str, Any]:
    p = get_manifest_path(playlist_id)
    if p.exists():
        return json.loads(p.read_text())
    return {
        "playlist_id": playlist_id,
        "playlist_title": "",
        "last_updated": None,
        "tracks": {},
    }


def save_manifest(manifest: Dict[str, Any]) -> None:
    manifest["last_updated"] = datetime.now().isoformat()
    p = get_manifest_path(manifest["playlist_id"])
    p.write_text(json.dumps(manifest, indent=2))


# -- Quiet Logging

class QuietLogger:
    def debug(self, msg):
        pass 

    def info(self, msg):
        pass
    
    def warning(self, msg):
        pass

    def error(self, msg):
        # uncomment to to see errors
        # console.print(f"[red]ERROR:[/red] {msg}")
        pass

# -- Progress helpers

def make_per_video_progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def make_progress_hook(
    manifest: Optional[Dict[str, Any]],
    per_video: Progress,
):
    """
    yt-dlp progress hook: updates rich progress bar and writes manifest.
    """
    task_id_map: Dict[str, int] = {}

    def hook(d):
        status = d.get("status")
        info = d.get("info_dict", {}) or {}
        vid_id = info.get("id")

        if status == "downloading":
            if vid_id and vid_id not in task_id_map:
                title = info.get("title", "unknown")
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                task_id_map[vid_id] = per_video.add_task(title, total=total)

            if vid_id in task_id_map:
                per_video.update(
                    task_id_map[vid_id],
                    completed=d.get("downloaded_bytes", 0),
                )

        elif status == "finished":
            if manifest is not None and vid_id:
                manifest["tracks"][vid_id] = {
                    "title": info.get("title", "Unknown"),
                    "filename": d.get("filename", ""),
                    "uploader": info.get("uploader", ""),
                    "downloaded_at": datetime.now().isoformat(),
                }
                save_manifest(manifest)

            if vid_id in task_id_map:
                task_id = task_id_map[vid_id]
                per_video.update(task_id, completed=per_video.tasks[task_id].total)
                per_video.refresh()

    return hook


def extract_playlist_id(url: str) -> Optional[str]:
    """Quickly fetch just the playlist ID without downloading."""
    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "extract_flat": True,
            "playlistend": 1,
            "ignoreerrors": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("id") if info else None


# -- Base yt-dlp options

def get_base_opts(
    output_dir: str,
    archive: bool,
    manifest: Optional[Dict[str, Any]],
    per_video: Optional[Progress],
    verbose: bool,
) -> Dict[str, Any]:
    quiet = not verbose
    no_warnings = not verbose

    opts: Dict[str, Any] = {
        "outtmpl": os.path.join(output_dir, "%(playlist_title)s/%(title)s.%(ext)s"),
        "ignoreerrors": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 2,
        "embedthumbnail": True,
        "addmetadata": True,
        "ratelimit": 5 * 1024 * 1024,  # 5 MiB/s
        "quiet": quiet,
        "no_warnings": no_warnings,
        "verbose": verbose,
    }
    if archive:
        opts["download_archive"] = ARCHIVE_FILE
    if manifest is not None and per_video is not None:
        opts["progress_hooks"] = [make_progress_hook(manifest, per_video)]
    if not verbose:
        opts["logger"] = QuietLogger()
    return opts


# -- CLI group

@click.group()
@click.option(
    "--verbose",
    is_flag=True,
    help="Show full yt-dlp logs and warnings.",
)
@click.pass_context
def cli(ctx, verbose):
    """ytget — YouTube downloader powered by yt-dlp"""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# -- Command: formats

@cli.command("formats")
@click.argument("url")
@click.pass_context
def list_formats(ctx, url):
    """List all available formats for a video."""
    verbose = ctx.obj.get("verbose", False)
    with yt_dlp.YoutubeDL({"quiet": not verbose, "no_warnings": not verbose}) as ydl:
        info = ydl.extract_info(url, download=False)

    table = Table(title=f"Formats: {info.get('title', url)}")
    table.add_column("ID", style="cyan")
    table.add_column("Ext", style="green")
    table.add_column("Resolution", style="magenta")
    table.add_column("FPS", style="yellow")
    table.add_column("Bitrate", style="blue")
    table.add_column("Note", style="white")

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


# -- Command: audio

@cli.command("audio")
@click.argument("url")
@click.option(
    "--format",
    "-f",
    "audio_format",
    default="mp3",
    type=click.Choice(["mp3", "flac", "opus", "m4a", "wav"]),
    help="Audio codec (default: mp3)",
)
@click.option(
    "--quality",
    "-q",
    default="0",
    help="Audio quality: 0=best VBR, 9=worst, or e.g. '320K'",
)
@click.option("--output", "-o", default=DEFAULT_OUTPUT_DIR, help="Output directory")
@click.option("--no-archive", is_flag=True, help="Skip download archive check")
@click.option("--sponsorblock", is_flag=True, help="Remove sponsor/intro/outro segments")
@click.option("--playlist-start", default=1, help="Start at playlist index")
@click.option("--playlist-end", default=None, type=int, help="End at playlist index")
@click.pass_context
def download_audio(
    ctx,
    url,
    audio_format,
    quality,
    output,
    no_archive,
    sponsorblock,
    playlist_start,
    playlist_end,
):
    """Download audio only. Works for single videos and full playlists."""
    verbose = ctx.obj.get("verbose", False)

    playlist_id = extract_playlist_id(url)
    manifest = load_manifest(playlist_id) if playlist_id else None
    if manifest and not manifest.get("playlist_title"):
        manifest["playlist_title"] = playlist_id

    per_video = make_per_video_progress()

    opts = get_base_opts(
        output,
        not no_archive,
        manifest,
        per_video,
        verbose,
    )
    opts.update(
        {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": quality,
                },
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "playlist_items": f"{playlist_start}:{playlist_end}"
            if playlist_end
            else f"{playlist_start}:",
        }
    )

    if sponsorblock:
        opts["postprocessors"] += [
            {
                "key": "SponsorBlock",
                "categories": ["sponsor", "intro", "outro", "selfpromo"],
            },
            {
                "key": "ModifyChapters",
                "remove_sponsor_segments": [
                    "sponsor",
                    "intro",
                    "outro",
                    "selfpromo",
                ],
            },
        ]

    console.rule(f"[bold green]Downloading Audio → {audio_format.upper()}")

    with per_video:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    if manifest:
        console.print(
            f"\n[dim]Manifest saved → {get_manifest_path(playlist_id)}[/dim]"
        )


# -- Command: video

@cli.command("video")
@click.argument("url")
@click.option(
    "--resolution",
    "-r",
    default="1080",
    type=click.Choice(["480", "720", "1080", "1440", "2160", "best"]),
    help="Max video resolution (default: 1080p)",
)
@click.option(
    "--format-ext",
    "-e",
    default="mp4",
    type=click.Choice(["mp4", "mkv", "webm"]),
)
@click.option("--output", "-o", default=DEFAULT_OUTPUT_DIR)
@click.option("--no-archive", is_flag=True)
@click.option("--subs", is_flag=True, help="Embed English subtitles")
@click.option("--playlist-start", default=1)
@click.option("--playlist-end", default=None, type=int)
@click.pass_context
def download_video(
    ctx,
    url,
    resolution,
    format_ext,
    output,
    no_archive,
    subs,
    playlist_start,
    playlist_end,
):
    """Download video + audio. Works for single videos and playlists."""
    verbose = ctx.obj.get("verbose", False)

    playlist_id = extract_playlist_id(url)
    manifest = load_manifest(playlist_id) if playlist_id else None

    if resolution == "best":
        fmt = "bestvideo+bestaudio/best"
    else:
        fmt = (
            f"bestvideo[height<={resolution}]+"
            f"bestaudio/best[height<={resolution}]"
        )

    per_video = make_per_video_progress()

    opts = get_base_opts(
        output,
        not no_archive,
        manifest,
        per_video,
        verbose,
    )
    opts.update(
        {
            "format": fmt,
            "merge_output_format": format_ext,
            "postprocessors": [
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "playlist_items": f"{playlist_start}:{playlist_end}"
            if playlist_end
            else f"{playlist_start}:",
        }
    )
    if subs:
        opts.update(
            {
                "writesubtitles": True,
                "subtitleslangs": ["en"],
                "embedsubtitles": True,
            }
        )
        opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    console.rule(f"[bold cyan]Downloading Video → {resolution}p {format_ext.upper()}")

    with per_video:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    if manifest:
        console.print(
            f"\n[dim]Manifest saved → {get_manifest_path(playlist_id)}[/dim]"
        )


# -- Command: info

@cli.command("info")
@click.argument("url")
@click.pass_context
def playlist_info(ctx, url):
    """Show playlist/video metadata without downloading."""
    verbose = ctx.obj.get("verbose", False)
    with yt_dlp.YoutubeDL(
        {"quiet": not verbose, "no_warnings": not verbose, "extract_flat": True}
    ) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = info.get("entries", [])
        console.print(f"\n[bold]Playlist:[/bold] {info.get('title')}")
        console.print(f"[bold]Count:[/bold] {len(entries)} videos\n")
        table = Table()
        table.add_column("#", style="dim")
        table.add_column("Title", style="white")
        table.add_column("Duration", style="green")
        for i, e in enumerate(entries or [], 1):
            if not e:
                continue
            dur = e.get("duration")
            dur_str = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "?"
            table.add_row(str(i), e.get("title", "Unknown"), dur_str)
        console.print(table)
    else:
        console.print(f"\n[bold]Title:[/bold]    {info.get('title')}")
        console.print(f"[bold]Channel:[/bold]  {info.get('uploader')}")
        console.print(f"[bold]Duration:[/bold] {info.get('duration_string')}")
        console.print(f"[bold]Views:[/bold]    {info.get('view_count'):,}\n")


# -- Command: check

@cli.command("check")
@click.argument("url")
@click.option(
    "--output", "-o", default=DEFAULT_OUTPUT_DIR, help="Your local music directory"
)
@click.pass_context
def check_playlist(ctx, url, output):
    """
    Inspect a playlist against your local manifest.

    Reports:
      - REMOVED tracks (in your manifest, gone from YouTube — check local file)
      - MISSING FILES (downloaded before, file deleted locally)
      - UNAVAILABLE ON YOUTUBE (blocked / age-gated / deleted entries currently in playlist)
    """
    verbose = ctx.obj.get("verbose", False)

    console.rule("[bold yellow]Fetching current playlist from YouTube...")

    with yt_dlp.YoutubeDL(
        {
            "quiet": not verbose,
            "no_warnings": not verbose,
            "extract_flat": True,
            "ignoreerrors": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        console.print("[red]Could not fetch playlist info.")
        return

    playlist_id = info.get("id")
    playlist_title = info.get("title", playlist_id)
    manifest = load_manifest(playlist_id)

    entries = [e for e in (info.get("entries") or []) if e]
    # IDs currently visible on YouTube (including blocked/age-gated)
    current_ids = {e.get("id") for e in entries if e.get("id")}
    tracked_keys = set(manifest.get("tracks", {}).keys())

    # --- Removed from playlist (was in manifest, no longer in playlist JSON)
    removed_keys = tracked_keys - current_ids

    removed_with_file = []
    removed_without_file = []
    for key in removed_keys:
        track = manifest["tracks"][key]
        filepath = Path(track.get("filename", ""))
        if filepath.exists():
            removed_with_file.append((key, track))
        else:
            removed_without_file.append((key, track))

    # --- Missing local files for tracks that ARE still in playlist
    missing_files = []
    for key in tracked_keys & current_ids:
        track = manifest["tracks"][key]
        filepath = Path(track.get("filename", ""))
        if track.get("filename") and not filepath.exists():
            missing_files.append((key, track))

    # --- Entries that yt-dlp marks as unavailable directly in playlist JSON
    # (titles like [Deleted video], or entries missing a URL/id)
    unavailable = []
    for e in entries:
        title = e.get("title")
        vid_id = e.get("id")
        if not vid_id:
            continue
        if title in ("[Deleted video]", "[Private video]", None):
            unavailable.append((vid_id, title or "Unavailable"))

    # --- Report

    console.print(
        f"\n[bold]Playlist:[/bold] {playlist_title}  ([dim]{playlist_id}[/dim])"
    )
    console.print(
        f"[bold]Last synced:[/bold] {manifest.get('last_updated', 'Never')}\n"
    )

    # Removed from YouTube (relative to manifest)
    if removed_with_file:
        console.print(
            f"[bold yellow]⚠  {len(removed_with_file)} track(s) removed from YouTube — local copy preserved:[/yellow bold]"
        )
        for key, track in removed_with_file:
            console.print(f"  [yellow]![/yellow] {track['title']}")
            console.print(f"      [dim]File:       {track['filename']}[/dim]")
            console.print(
                f"      [dim]Downloaded: {track['downloaded_at'][:10]}[/dim]"
            )

    if removed_without_file:
        console.print(
            f"\n[bold red]✗  {len(removed_without_file)} track(s) removed from YouTube AND local file missing:[/red bold]"
        )
        for key, track in removed_without_file:
            console.print(
                f"  [red]✗[/red] {track['title']}  [dim](downloaded {track['downloaded_at'][:10]})[/dim]"
            )

    if not removed_with_file and not removed_without_file:
        console.print("[green]✓ No tracks removed from YouTube (relative to manifest).[/green]")

    console.print()

    # Missing local files
    if missing_files:
        console.print(
            f"[bold red]⚠  {len(missing_files)} track(s) exist in manifest but local file is missing:[/red bold]"
        )
        for key, track in missing_files:
            console.print(
                f"  [red]-[/red] {track['title']}  [dim]({track.get('filename','?')})[/dim]"
            )
    else:
        console.print("[green]✓ No missing local files recorded in manifest.[/green]")

    console.print()

    # Unavailable entries in the playlist JSON itself
    if unavailable:
        console.print(
            f"[bold yellow]⚠  {len(unavailable)} entry/entries in playlist are currently unavailable on YouTube:[/yellow bold]"
        )
        for vid_id, title in unavailable:
            console.print(
                f"  [yellow]![/yellow] {title}  [dim](https://youtu.be/{vid_id})[/dim]"
            )

    console.print(f"\n[dim]Manifest: {get_manifest_path(playlist_id)}[/dim]")
    console.print(
        "[dim]Note: yt-dlp's download archive controls which items are treated as 'new'. "
        "Re-run 'ytget audio' on this playlist to fetch only new tracks.[/dim]"
    )


# -- Command: archive

@cli.command("archive")
@click.option("--clear", is_flag=True, help="Clear the download archive")
@click.option("--show", is_flag=True, help="Show archive stats")
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


# -- Entry point

def main():
    cli()
