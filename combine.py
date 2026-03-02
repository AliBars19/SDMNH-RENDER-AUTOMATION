import logging
import os
import json
import random
import yaml
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# ── Suppress Node.js console windows from pytubefix ──────────────────────────
# pytubefix spawns node.exe via subprocess.Popen / check_output for signature
# decryption and bot-guard but never passes CREATE_NO_WINDOW.  Monkey-patch
# before importing pytubefix so every child process is hidden on Windows.
if _NO_WINDOW:
    _real_Popen = subprocess.Popen
    _real_check_output = subprocess.check_output

    class _SilentPopen(_real_Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault('creationflags', _NO_WINDOW)
            super().__init__(*args, **kwargs)

    def _silent_check_output(*args, **kwargs):
        kwargs.setdefault('creationflags', _NO_WINDOW)
        return _real_check_output(*args, **kwargs)

    subprocess.Popen = _SilentPopen
    subprocess.check_output = _silent_check_output

from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from pytubefix import YouTube
from pytubefix.cli import on_progress
import time

from collections import OrderedDict

from src.database import Database, Video, Compilation, compilation_videos

console = Console()


def _sort_by_upload_date(video_files, videos):
    """Return video_files ordered newest-first using upload_date from the DB rows."""
    date_lookup = {v.id: v.upload_date for v in videos}
    sorted_ids = sorted(
        video_files.keys(),
        key=lambda vid: date_lookup.get(vid) or datetime.min,
        reverse=True,
    )
    return OrderedDict((vid, video_files[vid]) for vid in sorted_ids)


def load_config(config_path="config/config.yaml"):
    if not os.path.exists(config_path):
        console.print(f"[red]Error: {config_path} not found[/red]")
        exit(1)

    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe. Returns 0.0 on failure."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
        data = json.loads(result.stdout)
        duration = data.get('format', {}).get('duration')
        if duration:
            return float(duration)
    except Exception:
        pass
    return 0.0


def is_valid_video(video_path):
    """Quick ffprobe check that a file has valid video+audio streams and nonzero duration."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-show_format', str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
        data = json.loads(result.stdout)

        duration = float(data.get('format', {}).get('duration', 0))
        if duration <= 0:
            return False

        stream_types = {s.get('codec_type') for s in data.get('streams', [])}
        return 'video' in stream_types and 'audio' in stream_types
    except Exception:
        return False


def _get_cooldown_video_ids(session, cooldown_days):
    """Return a set of video IDs used in compilations within the cooldown window (single query)."""
    cooldown_date = datetime.utcnow() - timedelta(days=cooldown_days)
    rows = (
        session.query(compilation_videos.c.video_id)
        .join(Compilation)
        .filter(Compilation.created_at >= cooldown_date)
        .all()
    )
    return {r[0] for r in rows}


def select_videos(session, topic, count, cooldown_days):
    """Interactive mode: select up to `count` videos for a topic."""
    all_videos = session.query(Video).filter(Video.topic == topic).order_by(
        Video.upload_date.desc()
    ).all()

    if not all_videos:
        return []

    on_cooldown = _get_cooldown_video_ids(session, cooldown_days)

    available = [v for v in all_videos if v.id not in on_cooldown]

    # If not enough, use older ones too
    if len(available) < count:
        used = [v for v in all_videos if v.id in on_cooldown]
        available.extend(used[:count - len(available)])

    return available[:count]


def select_videos_within_duration(session, topic, max_duration_seconds, cooldown_days):
    """
    Automated mode: select videos for the topic whose TOTAL duration stays
    at or under max_duration_seconds.  Checked using DB duration values so
    nothing is downloaded before the limit is verified.

    Available (non-cooldown) videos are shuffled for variety, then cooldown
    overflow is appended as a fallback.  Videos with no duration data are
    assumed to be 1 hour each.
    """
    DEFAULT_DURATION = 3600  # seconds assumed when duration is NULL in DB

    all_videos = session.query(Video).filter(Video.topic == topic).order_by(
        Video.upload_date.desc()
    ).all()

    if not all_videos:
        return []

    on_cooldown = _get_cooldown_video_ids(session, cooldown_days)

    available = []
    cooldown_overflow = []

    for video in all_videos:
        if video.id not in on_cooldown:
            available.append(video)
        else:
            cooldown_overflow.append(video)

    # Shuffle each bucket separately so selection varies per run
    random.shuffle(available)
    random.shuffle(cooldown_overflow)

    # Prioritise fresh videos; fall back to cooldown overflow if needed
    candidates = available + cooldown_overflow

    selected = []
    total_duration = 0

    for video in candidates:
        vid_dur = video.duration if (video.duration and video.duration > 0) else DEFAULT_DURATION

        if total_duration + vid_dur <= max_duration_seconds:
            selected.append(video)
            total_duration += vid_dur

    hours = total_duration / 3600
    console.print(
        f"[dim]  Estimated total: {hours:.1f}h across {len(selected)} videos "
        f"(limit {max_duration_seconds/3600:.0f}h)[/dim]"
    )
    return selected


def sanitize_filename(filename):
    """Remove invalid characters from filename."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    return filename[:200]


def download_video(video, download_path, use_oauth=True, retry_attempts=3):
    """Download video using pytubefix with OAuth — highest quality adaptive streams."""
    # Check if already downloaded (cache by youtube_id in filename)
    for file in Path(download_path).glob("*.mp4"):
        if video.youtube_id in file.name:
            console.print(f"[dim]✓ Cached: {video.title[:60]}[/dim]")
            return file

    console.print(f"[cyan]Downloading: {video.title[:60]}[/cyan]")

    for attempt in range(retry_attempts):
        try:
            yt = YouTube(
                video.url,
                use_oauth=use_oauth,
                allow_oauth_cache=True
            )

            # Try adaptive streams first (best quality)
            video_stream = yt.streams.filter(
                adaptive=True,
                file_extension='mp4',
                only_video=True
            ).order_by('resolution').desc().first()

            audio_stream = yt.streams.filter(
                adaptive=True,
                only_audio=True
            ).order_by('abr').desc().first()

            if video_stream and audio_stream:
                console.print(f"[dim]  Quality: {video_stream.resolution}[/dim]")

                safe_title = sanitize_filename(yt.title)
                video_file = Path(download_path) / f"temp_video_{video.youtube_id}.mp4"
                audio_file = Path(download_path) / f"temp_audio_{video.youtube_id}.m4a"
                output_file = Path(download_path) / f"{safe_title}_{video.youtube_id}.mp4"

                video_stream.download(output_path=download_path, filename=f"temp_video_{video.youtube_id}.mp4")
                audio_stream.download(output_path=download_path, filename=f"temp_audio_{video.youtube_id}.m4a")

                # Normalize to 1080p/30fps/AAC so all downloads are uniform
                # and stream-copy concat (Method 1) works reliably every time.
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(video_file),
                    "-i", str(audio_file),
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                           "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-threads", "4",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    str(output_file)
                ]
                subprocess.run(cmd, check=True, capture_output=True, creationflags=_NO_WINDOW)

                video_file.unlink()
                audio_file.unlink()

                console.print(f"[green]  ✓ Downloaded + normalized ({video_stream.resolution} → 1080p)[/green]")
                return output_file

            # Fallback to progressive stream
            console.print(f"[yellow]  No adaptive streams, using progressive[/yellow]")
            stream = yt.streams.filter(
                progressive=True,
                file_extension='mp4'
            ).order_by('resolution').desc().first()

            if not stream:
                console.print(f"[red]  No streams available[/red]")
                return None

            safe_title = sanitize_filename(yt.title)
            raw_file = Path(download_path) / f"temp_prog_{video.youtube_id}.mp4"
            output_file = Path(download_path) / f"{safe_title}_{video.youtube_id}.mp4"

            console.print(f"[dim]  Resolution: {stream.resolution}[/dim]")
            stream.download(output_path=download_path, filename=f"temp_prog_{video.youtube_id}.mp4")

            if raw_file.exists():
                # Normalize progressive stream to 1080p/30fps too
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(raw_file),
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                           "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-threads", "4",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    str(output_file)
                ]
                subprocess.run(cmd, check=True, capture_output=True, creationflags=_NO_WINDOW)
                raw_file.unlink()
                console.print(f"[green]  ✓ Downloaded + normalized ({stream.resolution} → 1080p)[/green]")
                return output_file
            else:
                raise Exception("Download file not found after write")

        except Exception as e:
            error_msg = str(e).lower()

            if "bot" in error_msg:
                if attempt == 0 and not use_oauth:
                    console.print(f"[yellow]  Bot detected, retrying with OAuth...[/yellow]")
                    return download_video(video, download_path, use_oauth=True, retry_attempts=retry_attempts - 1)
                console.print(f"[yellow]  Bot detection with login — waiting 30s...[/yellow]")
                time.sleep(30)
            elif "400" in error_msg:
                time.sleep(5)
            elif "429" in error_msg:
                console.print(f"[yellow]  Rate limited — waiting 20s...[/yellow]")
                time.sleep(20)

            if attempt < retry_attempts - 1:
                console.print(f"[yellow]  Retry {attempt + 2}/{retry_attempts}...[/yellow]")
                time.sleep(3)
            else:
                console.print(f"[red]  Failed: {str(e)[:100]}[/red]")
                return None

    return None


def download_videos_sequential(videos, download_path, use_oauth=True):
    downloaded = {}

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Downloading videos...", total=len(videos))

        for video in videos:
            try:
                filepath = download_video(video, download_path, use_oauth=use_oauth)
                if filepath and is_valid_video(filepath):
                    downloaded[video.id] = filepath
                elif filepath:
                    console.print(f"[yellow]  Discarding corrupt file: {video.title[:50]}[/yellow]")
                    Path(filepath).unlink(missing_ok=True)
                time.sleep(2)  # Gentle rate limiting between downloads
            except Exception as e:
                console.print(f"[red]Error: {video.title[:40]}: {e}[/red]")
            finally:
                progress.update(task, advance=1)

    return downloaded


def download_videos_parallel(videos, download_path, max_workers=3, use_oauth=True):
    """Download videos using a thread pool for parallel downloads."""
    downloaded = {}

    def _download_one(video):
        try:
            filepath = download_video(video, download_path, use_oauth=use_oauth)
            if filepath and not is_valid_video(filepath):
                console.print(f"[yellow]  Discarding corrupt file: {video.title[:50]}[/yellow]")
                Path(filepath).unlink(missing_ok=True)
                return video.id, None
            return video.id, filepath
        except Exception as e:
            console.print(f"[red]Error: {video.title[:40]}: {e}[/red]")
            return video.id, None

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task(
            f"[cyan]Downloading videos ({max_workers} parallel)...",
            total=len(videos),
        )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_download_one, v): v for v in videos}
            for future in as_completed(futures):
                vid_id, filepath = future.result()
                if filepath:
                    downloaded[vid_id] = filepath
                progress.update(task, advance=1)

    return downloaded


def compile_videos(video_files, topic, output_path, auto_mode=False):
    """
    Compile downloaded videos into a single MP4 using FFmpeg.

    auto_mode=True  — skips all interactive prompts; proceeds with full
                      re-encode if earlier methods fail.
    auto_mode=False — original interactive behaviour (asks before method 3).
    """
    if not video_files:
        console.print("[red]No videos to compile[/red]")
        return None

    concat_file = Path(output_path) / "concat_list.txt"

    content = []
    for filepath in video_files.values():
        path_str = str(filepath.absolute()).replace('\\', '/')
        content.append(f"file '{path_str}'")

    with open(concat_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(content))

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = f"{topic}_compilation_{timestamp}.mp4"
    output_filepath = Path(output_path) / output_filename

    console.print(f"\n[cyan]Compiling {len(video_files)} videos...[/cyan]")

    # ── METHOD 1: Stream-copy with timestamp correction (fast, no quality loss) ──
    console.print("[dim]  Attempting stream-copy with timestamp correction...[/dim]")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-fflags", "+genpts+igndts",
        "-avoid_negative_ts", "make_zero",
        "-max_muxing_queue_size", "9999",
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_filepath)
    ]
    m1_timeout = None if auto_mode else 600
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=m1_timeout, creationflags=_NO_WINDOW)
        m1_ok = result.returncode == 0 and output_filepath.exists() and output_filepath.stat().st_size > 1_000_000
    except subprocess.TimeoutExpired:
        console.print("[yellow]  Stream-copy timed out — falling through to re-encode[/yellow]")
        m1_ok = False
        result = None

    if m1_ok:
        console.print(f"[green]✓ Fast compilation successful[/green]")
        return output_filepath

    if result is not None and result.returncode != 0 and result.stderr:
        logging.warning(f"FFmpeg method 1 stderr: {result.stderr[:500]}")

    if output_filepath.exists():
        output_filepath.unlink()

    # ── METHOD 2: Frame-drop filter + fast re-encode ──
    console.print("[yellow]  Stream-copy failed — trying frame-drop re-encode...[/yellow]")
    if output_filepath.exists():
        output_filepath.unlink()

    # No timeout in auto_mode since a 12-hour video can take hours to encode
    m2_timeout = None if auto_mode else 3600

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vf", "mpdecimate,setpts=N/FRAME_RATE/TB",
        "-vsync", "cfr",
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-threads", "4",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_filepath)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=m2_timeout, creationflags=_NO_WINDOW)

    if result.returncode == 0 and output_filepath.exists() and output_filepath.stat().st_size > 1_000_000:
        console.print(f"[green]✓ Compilation successful (frame-drop re-encode)[/green]")
        return output_filepath

    if result.stderr:
        logging.warning(f"FFmpeg method 2 stderr: {result.stderr[:500]}")

    if output_filepath.exists():
        output_filepath.unlink()

    # ── METHOD 3: Full re-encode with normalisation (last resort) ──
    if not auto_mode:
        console.print("[yellow]  Trying full re-encode (normalises all resolutions/framerates).[/yellow]")
        response = input("  Continue with full re-encode? (y/N): ").strip().lower()
        if response != 'y':
            console.print("[red]✗ Compilation cancelled[/red]")
            if result.stderr:
                console.print(f"[dim]Error: {result.stderr[:200]}[/dim]")
            return None
    else:
        console.print("[yellow]  Attempting full re-encode (auto mode)...[/yellow]")

    if output_filepath.exists():
        output_filepath.unlink()

    m3_timeout = None if auto_mode else 7200

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vf", (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-threads", "4",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        str(output_filepath)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=m3_timeout, creationflags=_NO_WINDOW)

    if result.returncode == 0 and output_filepath.exists() and output_filepath.stat().st_size > 1_000_000:
        console.print(f"[green]✓ Compilation successful (full re-encode)[/green]")
        return output_filepath

    console.print("[red]✗ All compilation methods failed[/red]")
    if result.stderr:
        logging.error(f"FFmpeg method 3 stderr: {result.stderr[:500]}")
        console.print(f"[red]Error: {result.stderr[:300]}[/red]")
    return None


def cleanup_stale_temps(download_path):
    """Remove leftover temp files from crashed previous runs."""
    removed = 0
    dl = Path(download_path)
    for pattern in ("temp_video_*.mp4", "temp_audio_*.m4a", "temp_prog_*.mp4"):
        for f in dl.glob(pattern):
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    if removed:
        console.print(f"[dim]Cleaned up {removed} stale temp files from a previous run[/dim]")


def cleanup_downloads(download_path, keep_files=None):
    keep_set = set(keep_files) if keep_files else set()
    removed = 0

    for file in Path(download_path).glob("*.mp4"):
        if file not in keep_set:
            try:
                file.unlink()
                removed += 1
            except Exception:
                pass

    if removed > 0:
        console.print(f"[dim]Cleaned up {removed} temporary files[/dim]")


def run_auto(topic, max_hours=12, cfg=None):
    """
    Fully non-interactive compilation run used by automation.py.

    Selects videos within the duration limit, downloads them, compiles,
    records the compilation in the database, cleans up downloads, and
    returns (output_file: Path, total_seconds: float, selected_videos: list).

    Raises Exception on any unrecoverable failure.
    """
    if cfg is None:
        cfg = load_config()

    db = Database(cfg['db_path'])
    session = db.get_session()

    os.makedirs(cfg['download_path'], exist_ok=True)
    os.makedirs(cfg['output_path'], exist_ok=True)

    # ── Sweep leftover temp files from crashed previous runs ──
    cleanup_stale_temps(cfg['download_path'])

    max_duration_seconds = int(max_hours * 3600)
    cooldown_days = cfg.get('cooldown_days', 30)

    # ── Select videos within the 12-hour cap ──
    console.print(f"\n[bold cyan]Topic:[/bold cyan] {topic}")
    videos = select_videos_within_duration(session, topic, max_duration_seconds, cooldown_days)

    if not videos:
        session.close()
        raise Exception(f"No videos found for topic '{topic}' in the database. Run update_db.py first.")

    console.print(f"[green]✓ Selected {len(videos)} videos[/green]")

    # ── Disk space pre-check ──
    # Estimate: ~500 MB/hour of source video after 1080p normalization,
    # plus the compiled output is roughly the same size.  Factor of 2.5 for safety.
    import shutil
    est_total_seconds = sum((v.duration or 3600) for v in videos)
    est_gb_needed = (est_total_seconds / 3600) * 0.5 * 2.5
    disk = shutil.disk_usage(cfg['download_path'])
    free_gb = disk.free / (1024 ** 3)
    if free_gb < est_gb_needed:
        session.close()
        raise Exception(
            f"Not enough disk space: ~{est_gb_needed:.1f} GB needed, "
            f"{free_gb:.1f} GB free. Free up space or reduce max_compilation_hours."
        )
    console.print(f"[dim]  Disk space OK: ~{est_gb_needed:.1f} GB needed, {free_gb:.1f} GB free[/dim]")

    # ── Download (parallel in auto mode) ──
    max_workers = cfg.get('max_concurrent_downloads', 3)
    video_files = download_videos_parallel(
        videos, cfg['download_path'], max_workers=max_workers, use_oauth=True,
    )

    if not video_files:
        session.close()
        raise Exception("No videos downloaded — check network and OAuth credentials.")

    console.print(f"[green]Downloaded {len(video_files)}/{len(videos)} videos[/green]")

    # ── Sort newest-first so the compilation opens with recent content ──
    video_files = _sort_by_upload_date(video_files, videos)

    # ── Compile (auto mode — no interactive prompts) ──
    output_file = compile_videos(video_files, topic, cfg['output_path'], auto_mode=True)

    if not output_file:
        cleanup_downloads(cfg['download_path'])
        session.close()
        raise Exception("Compilation failed for all methods.")

    # ── Get actual compiled duration ──
    total_seconds = get_video_duration(output_file)
    if total_seconds == 0:
        # Fall back to summing DB durations
        total_seconds = sum(
            (v.duration or 3600) for v in videos if v.id in video_files
        )

    # Capture youtube_ids as plain strings while the session is still open.
    # After session.commit() SQLAlchemy expires all ORM attributes, and accessing
    # them on detached objects outside this function raises DetachedInstanceError.
    selected_youtube_ids = [v.youtube_id for v in videos]

    # ── Record compilation in database ──
    try:
        compilation = Compilation(
            topic=topic,
            filename=output_file.name,
            video_count=len(video_files)
        )
        session.add(compilation)
        session.flush()

        for video_id in video_files.keys():
            stmt = compilation_videos.insert().values(
                compilation_id=compilation.id,
                video_id=video_id
            )
            session.execute(stmt)

        session.commit()
    except Exception as e:
        console.print(f"[yellow]Warning: could not save compilation record: {e}[/yellow]")
    finally:
        session.close()

    # ── Clean up source downloads ──
    cleanup_downloads(cfg['download_path'])

    return output_file, total_seconds, selected_youtube_ids


# ── Interactive CLI entry-point (unchanged behaviour) ─────────────────────────

def main():
    console.print("\n[bold cyan]🎬 SDMNH Video Compilation[/bold cyan]\n")

    console.print("[yellow]This script uses OAuth to log in to YouTube for downloading.[/yellow]")
    use_oauth_input = input("\nUse OAuth login? (Y/n): ").strip().lower()
    use_oauth = use_oauth_input != 'n'

    if use_oauth:
        console.print("[green]✓ Will use OAuth (login via browser)[/green]\n")
    else:
        console.print("[yellow]⚠ Will try without login (may fail)[/yellow]\n")

    cfg = load_config()
    db = Database(cfg['db_path'])
    session = db.get_session()

    os.makedirs(cfg['download_path'], exist_ok=True)
    os.makedirs(cfg['output_path'], exist_ok=True)

    topics = list(cfg['topics'].keys())
    console.print(f"[cyan]Available topics:[/cyan] {', '.join(topics)}")

    topic = input("\nEnter topic: ").strip().lower()
    if topic not in topics:
        console.print(f"[red]Error: '{topic}' is not a configured topic[/red]")
        exit(1)

    try:
        count = int(input("How many videos? (default 10): ").strip() or "10")
    except ValueError:
        count = 10

    max_hours = cfg.get('max_compilation_hours', 12)
    console.print(f"\n[cyan]Selecting videos (max {max_hours}h total)...[/cyan]")
    videos = select_videos_within_duration(
        session, topic, int(max_hours * 3600), cfg.get('cooldown_days', 30)
    )
    # Honour the user's requested count too
    videos = videos[:count]

    if not videos:
        console.print(f"[yellow]No videos found for topic '{topic}'[/yellow]")
        exit(0)

    console.print(f"[green]✓ Selected {len(videos)} videos[/green]\n")

    video_files = download_videos_sequential(videos, cfg['download_path'], use_oauth=use_oauth)

    if not video_files:
        console.print("[red]No videos downloaded[/red]")
        exit(1)

    console.print(f"\n[green]✓ Downloaded {len(video_files)}/{len(videos)} videos[/green]")

    # ── Sort newest-first so the compilation opens with recent content ──
    video_files = _sort_by_upload_date(video_files, videos)

    output_file = compile_videos(video_files, topic, cfg['output_path'])

    if not output_file:
        exit(1)

    compilation = Compilation(
        topic=topic,
        filename=output_file.name,
        video_count=len(video_files)
    )
    session.add(compilation)
    session.flush()

    for video_id in video_files.keys():
        stmt = compilation_videos.insert().values(
            compilation_id=compilation.id,
            video_id=video_id
        )
        session.execute(stmt)

    session.commit()
    session.close()

    cleanup_downloads(cfg['download_path'], list(video_files.values()))

    console.print(f"\n[green bold]✅ Done![/green bold]")
    console.print(f"[green]Compilation saved: {output_file}[/green]\n")


if __name__ == "__main__":
    main()
