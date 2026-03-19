#!/usr/bin/env python3
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import click
import yt_dlp
from rich.console import Console
from rich.table import Table
from rich import print as rprint
from rich.panel import Panel
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
ARCHIVE_META_FILE = CONFIG_DIR / "archive_meta.json"
PLAYLISTS_FILE = CONFIG_DIR / "playlists.json"
DEFAULT_OUTPUT_DIR = "/home/preet/YTMedia"


# -- Quiet logger (suppresses yt-dlp internal error/warning output)
class QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


# -- Playlist registry helpers
def load_registry() -> Dict[str, Any]:
    if PLAYLISTS_FILE.exists():
        return json.loads(PLAYLISTS_FILE.read_text())
    return {"by_id": {}, "by_name": {}}


def save_registry(registry: Dict[str, Any]) -> None:
    PLAYLISTS_FILE.write_text(json.dumps(registry, indent=2))


def register_playlist(playlist_id: str, title: str, url: str) -> None:
    """
    Save a playlist into the registry.
    Detects title changes and updates the name mapping accordingly.
    """
    registry = load_registry()
    existing = registry["by_id"].get(playlist_id)

    if existing:
        old_name = existing.get("name", "")
        if old_name and old_name != title:
            # Title changed on YouTube — update by_name mapping
            console.print(
                f"[yellow]⚠  Playlist renamed on YouTube:[/yellow] "
                f"[dim]{old_name}[/dim] → [bold]{title}[/bold]"
            )
            # Remove old name key, keep old name as alias too
            if old_name in registry["by_name"]:
                del registry["by_name"][old_name]

    registry["by_id"][playlist_id] = {
        "name": title,
        "last_seen_title": title,
        "url": url,
    }
    registry["by_name"][title] = playlist_id
    save_registry(registry)


def resolve_target(target: str) -> Tuple[str, str]:
    """
    Resolve a target (URL or playlist name) to (url, playlist_id).

    - If target looks like a URL, fetch playlist_id from yt-dlp.
    - Otherwise, look it up by name in the registry.

    Returns (url, playlist_id).
    Raises click.UsageError if name is not found.
    """
    if target.startswith("http://") or target.startswith("https://"):
        return target, _fetch_playlist_id(target)

    # Treat as friendly name
    registry = load_registry()
    playlist_id = registry["by_name"].get(target)
    if not playlist_id:
        # Try case-insensitive match
        for name, pid in registry["by_name"].items():
            if name.lower() == target.lower():
                playlist_id = pid
                break

    if not playlist_id:
        known = list(registry["by_name"].keys())
        known_str = "\n  ".join(known) if known else "(none yet)"
        raise click.UsageError(
            f"Playlist name '{target}' not found in registry.\n"
            f"Known playlists:\n  {known_str}\n\n"
            f"Use the full URL the first time to register it."
        )

    entry = registry["by_id"][playlist_id]
    return entry["url"], playlist_id


def _fetch_playlist_id(url: str) -> Optional[str]:
    with yt_dlp.YoutubeDL(
        {
            **YDL_BASE,
            "quiet": True,
            "extract_flat": True,
            "playlistend": 1,
            "ignoreerrors": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("id") if info else None


def list_playlists() -> None:
    """Print all registered playlists as a rich table."""
    registry = load_registry()
    entries = registry.get("by_id", {})

    if not entries:
        console.print("[yellow]No playlists registered yet.[/yellow]")
        return

    table = Table(title="Registered Playlists")
    table.add_column("Name", style="white")
    table.add_column("Playlist ID", style="cyan")
    table.add_column("URL", style="dim")

    for pid, entry in entries.items():
        table.add_row(
            entry.get("name", "?"),
            pid,
            entry.get("url", "?"),
        )

    console.print(table)


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


# -- Manifest repair helper
def repair_manifest_paths(
    playlist_id: str, playlist_title: Optional[str] = None
) -> Tuple[int, int]:
    """
    Repair manifest filenames by mapping missing/transient paths to final audio files.
    Handles .webm, .m4a, .opus, and any other intermediate container left by yt-dlp.
    Returns (fixed_count, still_missing_count).
    """
    manifest_path = get_manifest_path(playlist_id)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text())
    tracks = manifest.get("tracks", {})
    priority = [".mp3", ".flac", ".opus", ".m4a", ".wav"]
    intermediate = {".webm", ".m4a", ".opus", ".ogg", ".mp4"}

    fixed = 0
    not_found = 0

    for key, track in tracks.items():
        fname = track.get("filename", "")
        if not fname:
            continue
        p = Path(fname)

        if p.exists():
            continue

        candidates = [
            p.with_suffix(ext) for ext in priority if p.with_suffix(ext).exists()
        ]
        if candidates:
            preferred = sorted(
                candidates, key=lambda c: priority.index(c.suffix.lower())
            )[0]
            track["filename"] = str(preferred)
            fixed += 1
        else:
            # Only count as "not found" if it was an intermediate container
            # (final .mp3 going missing is a real missing file, not a repair case)
            if p.suffix.lower() in intermediate:
                not_found += 1

    if playlist_title:
        manifest["playlist_title"] = playlist_title
    manifest["last_updated"] = datetime.now().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return fixed, not_found


# -- archive meta data functions
def load_archive_meta() -> Dict[str, Any]:
    if ARCHIVE_META_FILE.exists():
        return json.loads(ARCHIVE_META_FILE.read_text())
    return {}


def save_archive_meta(meta: Dict[str, Any]) -> None:
    ARCHIVE_META_FILE.write_text(json.dumps(meta, indent=2))


def register_archive_entry(
    title: str,
    vid_id: str,
    playlist_id: str,
    playlist_name: str,
) -> None:
    meta = load_archive_meta
    meta[vid_id] = {
        "title": title,
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "downloaded_at": datetime.now().isoformat(),
    }
    save_archive_meta(meta)


# -- Progress helpers
def make_per_video_progress() -> Progress:
    return Progress(
        TextColumn("[bold cyan]{task.description}"),
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
                title = info.get("title", "Unknown")
                manifest["tracks"][vid_id] = {
                    "title": title,
                    "filename": d.get("filename", ""),
                    "uploader": info.get("uploader", ""),
                    "downloaded_at": datetime.now().isoformat(),
                }
                save_manifest(manifest)
                register_archive_entry(
                    vid_id,
                    title,
                    manifest.get("playlist_id", ""),
                    manifest.get("playlist_title", ""),
                )
            if vid_id in task_id_map:
                task_id = task_id_map[vid_id]
                per_video.update(task_id, completed=per_video.tasks[task_id].total)
                per_video.refresh()

    return hook


# -- Postprocessor hook
def make_postprocessor_hook(per_video: Progress):
    """
    Creates a new progress task for each postprocessor step so every
    step persists as its own line.
    Deduplicates duplicate hook calls from yt-dlp internals.
    """
    active_tasks: Dict[tuple, int] = {}
    seen: set = set()

    def hook(d):
        status = d.get("status")
        info = d.get("info_dict", {}) or {}
        vid_id = info.get("id", "unknown")
        title = info.get("title", "unknown")
        pp = d.get("postprocessor", "Processing")

        key = (vid_id, pp, status)
        if key in seen:
            return
        seen.add(key)

        step_key = (vid_id, pp)

        if status == "started":
            task_id = per_video.add_task(
                f"[dim]⚙  {pp}:[/dim] {title}",
                total=None,  # indeterminate spinner
            )
            active_tasks[step_key] = task_id

        elif status == "finished":
            task_id = active_tasks.pop(step_key, None)
            if task_id is not None:
                per_video.update(
                    task_id,
                    description=f"[green]✓  {pp}:[/green] {title}",
                    total=1,
                    completed=1,
                )

    return hook


# -- Base yt-dlp options
# _NODE_PATH = shutil.which("node")
# YDL_BASE: Dict[str, Any] = {}
# if _NODE_PATH:
#     YDL_BASE["js_runtimes"] = {"node": {"path": _NODE_PATH}}
# YDL_BASE = {
#     "no_js_runtimes": True,
#     "js_runtimes": {"node": {"path": None}},
# }
YDL_BASE: Dict[str, Any] = {
    "logger": QuietLogger(),
    "config_locations": [str(Path.home() / ".config" / "yt-dlp" / "config")],
}


def get_base_opts(
    output_dir: str,
    archive: bool,
    manifest: Optional[Dict[str, Any]],
    per_video: Optional[Progress],
    verbose: bool,
    show_processing: bool = False,
) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        **YDL_BASE,
        "outtmpl": os.path.join(output_dir, "%(playlist_title)s/%(title)s.%(ext)s"),
        "ignoreerrors": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 2,
        # "embedthumbnail":                True,
        "addmetadata": True,
        "ratelimit": 5 * 1024 * 1024,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "verbose": verbose,
    }
    if archive:
        opts["download_archive"] = ARCHIVE_FILE
    if manifest is not None and per_video is not None:
        opts["progress_hooks"] = [make_progress_hook(manifest, per_video)]
        if show_processing:
            opts["postprocessor_hooks"] = [make_postprocessor_hook(per_video)]
    if not verbose:
        opts["logger"] = QuietLogger()
    return opts


# -- CLI group


@click.group()
@click.option("--verbose", is_flag=True, help="Show full yt-dlp logs and warnings.")
@click.pass_context
def cli(ctx, verbose):
    """ytget — YouTube downloader powered by yt-dlp"""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# -- Command: playlists


@cli.command("playlists")
def cmd_playlists():
    """List all registered playlists (use their name instead of URL in other commands)."""
    list_playlists()


# -- Command: formats


@cli.command("formats")
@click.argument("target")
@click.pass_context
def list_formats(ctx, target):
    """List all available formats for a video or playlist URL."""
    verbose = ctx.obj.get("verbose", False)
    url, _ = resolve_target(target)
    with yt_dlp.YoutubeDL(
        {**YDL_BASE, "quiet": not verbose, "no_warnings": not verbose}
    ) as ydl:
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
@click.argument("target")
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
@click.option("--output", "-o", default=DEFAULT_OUTPUT_DIR)
@click.option("--no-archive", is_flag=True)
@click.option("--sponsorblock", is_flag=True)
@click.option(
    "--show-processing",
    is_flag=True,
    default=False,
    help="Show FFmpeg postprocessing bars",
)
@click.option(
    "--thumbnail",
    is_flag=True,
    default=False,
    help="Embed YouTube thumbnail as cover art (extra processing required)",
)
@click.option("--playlist-start", default=1)
@click.option("--playlist-end", default=None, type=int)
@click.pass_context
def download_audio(
    ctx,
    target,
    audio_format,
    quality,
    output,
    no_archive,
    sponsorblock,
    show_processing,
    thumbnail,
    playlist_start,
    playlist_end,
):
    """Download audio only. Accepts a URL or a registered playlist name."""
    verbose = ctx.obj.get("verbose", False)
    url, playlist_id = resolve_target(target)

    # Fetch playlist info and register (handles renames)
    with yt_dlp.YoutubeDL(
        {
            **YDL_BASE,
            "quiet": True,
            "extract_flat": True,
            "playlistend": 1,
            "ignoreerrors": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)
    playlist_title = info.get("title", playlist_id) if info else playlist_id
    register_playlist(playlist_id, playlist_title, url)

    manifest = load_manifest(playlist_id)
    if not manifest.get("playlist_title"):
        manifest["playlist_title"] = playlist_title

    per_video = make_per_video_progress()
    opts = get_base_opts(
        output, not no_archive, manifest, per_video, verbose, show_processing
    )

    if info and info.get("_type") == "playlist":
        opts["outtmpl"] = os.path.join(output, "%(playlist_title)s/%(title)s.%(ext)s")
    else:
        opts["outtmpl"] = os.path.join(output, "%(title)s.%(ext)s")

    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": quality,
        },
        {"key": "FFmpegMetadata"},
    ]
    if thumbnail:
        postprocessors.append({"key": "EmbedThumbnail"})
        opts["embedthumbnail"] = True

    opts.update(
        {
            "format": "bestaudio/best",
            "postprocessors": postprocessors,
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
                "remove_sponsor_segments": ["sponsor", "intro", "outro", "selfpromo"],
            },
        ]

    # console.print(Panel(f"\n[bold green]Retrieving Video[/bold green] [cyan]{resolution}p[/cyan] · [dim]{playlist_title}[/dim]\n", expand=False))
    console.print(
        Panel(
            f"[bold green]Retrieving Audio[/bold green] · [cyan]{audio_format.upper()}[/cyan] · [dim]{playlist_title}[/dim]",
            expand=False,
        )
    )
    # with per_video:
    #     with yt_dlp.YoutubeDL(opts) as ydl:
    #         ydl.download([url])
    per_video.start()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    finally:
        per_video.stop()

    if manifest:
        console.print(f"\n[dim]Manifest saved → {get_manifest_path(playlist_id)}[/dim]")


# -- Command: video


@cli.command("video")
@click.argument("target")
@click.option(
    "--resolution",
    "-r",
    default="1080",
    type=click.Choice(["480", "720", "1080", "1440", "2160", "best"]),
)
@click.option(
    "--format-ext", "-e", default="mp4", type=click.Choice(["mp4", "mkv", "webm"])
)
@click.option("--output", "-o", default=DEFAULT_OUTPUT_DIR)
@click.option("--no-archive", is_flag=True)
@click.option("--subs", is_flag=True)
@click.option(
    "--show-processing",
    is_flag=True,
    default=False,
    help="Show FFmpeg postprocessing bars",
)
@click.option(
    "--thumbnail",
    is_flag=True,
    default=False,
    help="Embed YouTube thumbnail as cover art (extra processing required)",
)
@click.option("--playlist-start", default=1)
@click.option("--playlist-end", default=None, type=int)
@click.pass_context
def download_video(
    ctx,
    target,
    resolution,
    format_ext,
    output,
    no_archive,
    subs,
    show_processing,
    thumbnail,
    playlist_start,
    playlist_end,
):
    """Download video + audio. Accepts a URL or a registered playlist name."""
    verbose = ctx.obj.get("verbose", False)
    url, playlist_id = resolve_target(target)

    with yt_dlp.YoutubeDL(
        {
            **YDL_BASE,
            "quiet": True,
            "extract_flat": True,
            "playlistend": 1,
            "ignoreerrors": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)
    playlist_title = info.get("title", playlist_id) if info else playlist_id
    register_playlist(playlist_id, playlist_title, url)

    manifest = load_manifest(playlist_id) if playlist_id else None
    fmt = (
        "bestvideo+bestaudio/best"
        if resolution == "best"
        else f"bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]"
    )

    per_video = make_per_video_progress()
    opts = get_base_opts(
        output, not no_archive, manifest, per_video, verbose, show_processing
    )

    if info and info.get("_type") == "playlist":
        opts["outtmpl"] = os.path.join(output, "%(playlist_title)s/%(title)s.%(ext)s")
    else:
        opts["outtmpl"] = os.path.join(output, "%(title)s.%(ext)s")

    postprocessors = [{"key": "FFmpegMetadata"}]
    if thumbnail:
        postprocessors.append({"key": "EmbedThumbnail"})
        opts["embedthumbnail"] = True

    update = {
        "format": fmt,
        "merge_output_format": format_ext,
        "postprocessors": postprocessors,
    }
    if info and info.get("_type") == "playlist":
        update["playlist_items"] = (
            f"{playlist_start}:{playlist_end}" if playlist_end else f"{playlist_start}:"
        )
    opts.update(update)

    if subs:
        opts.update(
            {"writesubtitles": True, "subtitleslangs": ["en"], "embedsubtitles": True}
        )
        opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    console.print(
        Panel(
            f"[bold green]Retrieving Video[/bold green] · [cyan]{resolution}p[/cyan] · [dim]{playlist_title}[/dim]",
            expand=False,
        )
    )
    # console.print(Panel(f"\n[bold green]⬇ Audio[/bold green]  [cyan]{audio_format.upper()}[/cyan] · [dim]{playlist_title}[/dim]\n", expand=False))
    # with per_video:
    #     with yt_dlp.YoutubeDL(opts) as ydl:
    #         ydl.download([url])
    per_video.start()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    finally:
        per_video.stop()

    if manifest:
        console.print(f"\n[dim]Manifest saved → {get_manifest_path(playlist_id)}[/dim]")


# -- Command: info


@cli.command("info")
@click.argument("target")
@click.pass_context
def playlist_info(ctx, target):
    """Show playlist/video metadata without downloading."""
    verbose = ctx.obj.get("verbose", False)
    url, _ = resolve_target(target)

    with yt_dlp.YoutubeDL(
        {
            **YDL_BASE,
            "quiet": not verbose,
            "no_warnings": not verbose,
            "extract_flat": True,
        }
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
            dur_str = f"{int(dur) // 60}:{int(dur) % 60:02d}" if dur else "?"
            table.add_row(str(i), e.get("title", "Unknown"), dur_str)
        console.print(table)
    else:
        console.print(f"\n[bold]Title:[/bold]    {info.get('title')}")
        console.print(f"[bold]Channel:[/bold]  {info.get('uploader')}")
        console.print(f"[bold]Duration:[/bold] {info.get('duration_string')}")
        console.print(f"[bold]Views:[/bold]    {info.get('view_count'):,}\n")


# -- Command: remove


@cli.command("remove")
@click.argument("target")
@click.argument("search")
@click.option(
    "--delete-file",
    is_flag=True,
    default=False,
    help="Also delete the local file from drive",
)
@click.pass_context
def remove_track(ctx, target, search, delete_file):
    """
    Remove a track from the manifest, archive, and optionally disk.

    TARGET is the playlist name or URL.
    SEARCH is a partial title or exact video ID to match against.

    Example:
      ytget remove stuff_to_download "Cali Girl"
      ytget remove stuff_to_download 5QjIQnjRHPk
    """
    url, playlist_id = resolve_target(target)
    manifest = load_manifest(playlist_id)
    archive_meta = load_archive_meta()
    tracks = manifest.get("tracks", {})

    # Match by ID first, then by case-insensitive title substring
    matches = []
    for vid_id, track in tracks.items():
        if search.lower() == vid_id.lower():
            matches = [(vid_id, track)]
            break
        if search.lower() in track.get("title", "").lower():
            matches.append((vid_id, track))

    if not matches:
        console.print(f"[red]No tracks found matching:[/red] {search}")
        return

    if len(matches) > 1:
        console.print(
            f"[yellow]Multiple matches for '{search}'. Narrow your search:[/yellow]\n"
        )
        for vid_id, track in matches:
            console.print(f"  [cyan]{vid_id}[/cyan]  {track.get('title', '?')}")
        return

    vid_id, track = matches[0]
    title = track.get("title", "?")
    filename = track.get("filename", "")

    console.print(f"\n[bold]Track:[/bold]  {title}")
    console.print(f"[bold]ID:[/bold]     {vid_id}")
    if filename:
        console.print(f"[bold]File:[/bold]   {filename}")
    console.print()

    if not click.confirm("Remove this track from archive and manifest?"):
        console.print("[dim]Aborted.[/dim]")
        return

    # 1. Remove from manifest
    del manifest["tracks"][vid_id]
    save_manifest(manifest)
    console.print("[green]✓ Removed from manifest.[/green]")

    # 2. Remove from archive.txt
    archive_path = Path(ARCHIVE_FILE)
    if archive_path.exists():
        lines = archive_path.read_text().splitlines()
        filtered = [l for l in lines if vid_id not in l]
        archive_path.write_text("\n".join(filtered) + "\n")
        removed = len(lines) - len(filtered)
        if removed:
            console.print("[green]✓ Removed from archive.txt.[/green]")
        else:
            console.print("[dim]• Not found in archive.txt (already clean).[/dim]")

    # 3. Remove from archive_meta.json
    if vid_id in archive_meta:
        del archive_meta[vid_id]
        save_archive_meta(archive_meta)
        console.print("[green]✓ Removed from archive metadata.[/green]")

    # 4. Optionally delete local file
    if delete_file and filename:
        filepath = Path(filename)
        if filepath.exists():
            filepath.unlink()
            console.print(f"[green]✓ Deleted file:[/green] {filename}")
        else:
            console.print(f"[yellow]• File not found on disk:[/yellow] {filename}")
    elif filename and not delete_file:
        console.print(
            "[dim]• Local file kept. Use --delete-file to also remove it from disk.[/dim]"
        )


# -- Command: check


@cli.command("check")
@click.argument("target")
@click.option("--output", "-o", default=DEFAULT_OUTPUT_DIR)
@click.pass_context
def check_playlist(ctx, target, output):
    """
    Inspect a playlist against your local manifest.

    Reports:
      - REMOVED tracks (in manifest, gone from YouTube — with local file status)
      - MISSING FILES (downloaded before, file deleted/moved locally)
    """
    verbose = ctx.obj.get("verbose", False)
    url, playlist_id = resolve_target(target)

    # auto repair the manifest
    manifest_path = get_manifest_path(playlist_id)
    if manifest_path.exists():
        fixed, _ = repair_manifest_paths(playlist_id)
        if fixed:
            console.print(
                f"[dim]Auto-repaired {fixed} stale manifest path(s) before `check`.[/dim]"
            )

    console.rule("[bold yellow]Fetching current playlist from YouTube...")

    with yt_dlp.YoutubeDL(
        {
            **YDL_BASE,
            "quiet": not verbose,
            "no_warnings": not verbose,
            "extract_flat": True,
            "ignoreerrors": True,
        }
    ) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        console.print("[red]Could not fetch playlist info.[/red]")
        return

    playlist_title = info.get("title", playlist_id)
    register_playlist(playlist_id, playlist_title, url)
    manifest = load_manifest(playlist_id)

    entries = [e for e in (info.get("entries") or []) if e]
    current_ids = {e.get("id") for e in entries if e.get("id")}
    tracked_keys = set(manifest.get("tracks", {}).keys())

    removed_keys = tracked_keys - current_ids
    removed_with_file, removed_without_file = [], []
    for key in removed_keys:
        track = manifest["tracks"][key]
        filepath = Path(track.get("filename", ""))
        (removed_with_file if filepath.exists() else removed_without_file).append(
            (key, track)
        )

    missing_files = []
    for key in tracked_keys & current_ids:
        track = manifest["tracks"][key]
        filepath = Path(track.get("filename", ""))
        if track.get("filename") and not filepath.exists():
            missing_files.append((key, track))

    console.print(
        f"\n[bold]Playlist:[/bold] {playlist_title}  ([dim]{playlist_id}[/dim])"
    )
    console.print(
        f"[bold]Last synced:[/bold] {manifest.get('last_updated', 'Never')}\n"
    )

    if removed_with_file:
        console.print(
            f"[bold yellow]⚠  {len(removed_with_file)} track(s) removed from YouTube — local copy preserved:[/yellow bold]"
        )
        for key, track in removed_with_file:
            console.print(f"  [yellow]![/yellow] {track['title']}")
            console.print(f"      [dim]File:       {track['filename']}[/dim]")
            console.print(f"      [dim]Downloaded: {track['downloaded_at'][:10]}[/dim]")

    if removed_without_file:
        console.print(
            f"\n[bold red]✗  {len(removed_without_file)} track(s) removed from YouTube AND local file missing:[/red bold]"
        )
        for key, track in removed_without_file:
            console.print(
                f"  [red]✗[/red] {track['title']}  [dim]({track.get('filename', '?')})[/dim]"
            )

    if not removed_with_file and not removed_without_file:
        console.print(
            "[green]✓ No tracks removed from YouTube (relative to manifest).[/green]"
        )

    console.print()

    if missing_files:
        console.print(
            f"[bold red]⚠  {len(missing_files)} track(s) exist in manifest but local file is missing:[/red bold]"
        )
        for key, track in missing_files:
            console.print(
                f"  [red]-[/red] {track['title']}  [dim]({track.get('filename', '?')})[/dim]"
            )
    else:
        console.print("[green]✓ No missing local files recorded in manifest.[/green]")

    console.print(f"\n[dim]Manifest: {get_manifest_path(playlist_id)}[/dim]")
    console.print(
        "[dim]Run 'ytget audio' on this playlist to fetch only new tracks.[/dim]"
    )


# -- Command: yt-unavailable


@cli.command("yt-unavailable")
@click.argument("target")
@click.pass_context
def list_unavailable(ctx, target):
    """
    List entries that are currently unavailable on YouTube in this playlist.
    If an unavailable entry was previously downloaded, shows the local filename.
    """
    verbose = ctx.obj.get("verbose", False)
    url, playlist_id = resolve_target(target)

    console.rule("[bold yellow]Scanning playlist for unavailable YouTube entries...")

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
        console.print("[red]Could not fetch playlist info.[/red]")
        return

    playlist_title = info.get("title", playlist_id)
    manifest = load_manifest(playlist_id)
    entries = [e for e in (info.get("entries") or []) if e]

    unavailable = []
    for e in entries:
        title = e.get("title")
        vid_id = e.get("id")
        if not vid_id:
            continue
        if title in ("[Deleted video]", "[Private video]", None):
            # Check if we previously downloaded this
            track = manifest.get("tracks", {}).get(vid_id)
            local_file = track.get("filename") if track else None
            local_title = track.get("title") if track else None
            unavailable.append(
                (vid_id, title or "Unavailable", local_title, local_file)
            )

    console.print(
        f"\n[bold]Playlist:[/bold] {playlist_title}  ([dim]{playlist_id}[/dim])\n"
    )

    if not unavailable:
        console.print(
            "[green]✓ No unavailable entries detected in this playlist.[/green]"
        )
        return

    console.print(
        f"[bold yellow]⚠  {len(unavailable)} entry/entries currently unavailable on YouTube:[/yellow bold]\n"
    )
    for vid_id, yt_title, local_title, local_file in unavailable:
        console.print(
            f"  [yellow]![/yellow] {yt_title}  [dim](https://youtu.be/{vid_id})[/dim]"
        )
        if local_title:
            console.print(f"      [dim]Original title: {local_title}[/dim]")
        if local_file:
            exists = Path(local_file).exists()
            status = "[green]exists[/green]" if exists else "[red]missing[/red]"
            console.print(f"      [dim]Local file ({status}): {local_file}[/dim]")


# -- Command: repair


@cli.command("repair")
@click.argument("target")
@click.pass_context
def repair_manifest_cmd(ctx, target):
    """
    Repair manifest filenames for a playlist.
    Maps missing/transient paths (.webm, .m4a, etc.) to existing audio files.
    """
    console.rule("[bold yellow]Repairing manifest for playlist...")
    url, playlist_id = resolve_target(target)

    with yt_dlp.YoutubeDL(
        {**YDL_BASE, "quiet": True, "extract_flat": True, "ignoreerrors": True}
    ) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        console.print("[red]Could not fetch playlist info from URL.[/red]")
        return

    playlist_title = info.get("title", playlist_id)
    register_playlist(playlist_id, playlist_title, url)

    try:
        fixed, not_found = repair_manifest_paths(playlist_id, playlist_title)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    console.print(
        f"\n[bold]Playlist:[/bold] {playlist_title}  ([dim]{playlist_id}[/dim])"
    )
    console.print(f"[bold]Manifest:[/bold] {get_manifest_path(playlist_id)}")
    console.print(f"[green]✓ Updated entries:[/green] {fixed}")
    console.print(
        f"[yellow]• Still pointing to non-existent intermediate file:[/yellow] {not_found}"
    )


# -- Command: archive-backfill
@cli.command("archive-backfill")
@click.pass_context
def archive_backfill(ctx):
    """Backfill archive_meta.json from existing manifests."""
    meta = load_archive_meta()
    manifests_dir = CONFIG_DIR / "manifests"
    filled = 0

    for manifest_file in manifests_dir.glob("*.json"):
        manifest = json.loads(manifest_file.read_text())
        playlist_name = manifest.get("playlist_title", manifest_file.stem)
        playlist_id = manifest.get("playlist_id", manifest_file.stem)

        for vid_id, track in manifest.get("tracks", {}).items():
            if vid_id not in meta:
                meta[vid_id] = {
                    "title": track.get("title", "Unknown"),
                    "playlist_id": playlist_id,
                    "playlist_name": playlist_name,
                    "downloaded_at": track.get("downloaded_at", ""),
                }
                filled += 1

    save_archive_meta(meta)
    console.print(
        f"[green]✓ Backfilled {filled} entries into archive_meta.json[/green]"
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
            table = Table(title="Archives")
            table.add_column("Video ID", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Playlist", style="white")
            table.add_column("Retrieved", style="dim")

            lines = archive_path.read_text().strip().splitlines()
            meta = load_archive_meta()
            unknown = 0

            for line in lines:
                vid_id = line.split()[-1] if line.strip() else ""
                entry = meta.get(vid_id, {})
                title = entry.get("title", "")
                raw_date = entry.get("downloaded_at", "")
                try:
                    date = datetime.fromisoformat(raw_date).strftime(
                        # "%m %d, %Y %I:%M %p"
                        "%I:%M %p, %m/%d/%Y "
                    )
                except (ValueError, TypeError):
                    date = "-"
                playlist = entry.get("playlist_name", "")

                if not title:
                    title = "[dim](no metadata)[/dim]"
                    unknown += 1

                table.add_row(vid_id, title, playlist, date)

            console.print(table)
            console.print(f"[bold]Archive:[/bold] {ARCHIVE_FILE}")
            console.print(f"[bold]Entries:[/bold] {len(lines)} videos tracked\n")

            if unknown:
                console.print(
                    f"\n[yellow]• {unknown} entries missing metadata. Run:[/yellow] ytget archive-backfill"
                )
        else:
            console.print("[yellow]No archive file found yet.[/yellow]")


# -- Entry point


def main():
    cli()
