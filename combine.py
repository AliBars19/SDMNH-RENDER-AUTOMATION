import os
import yaml
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

from src.database import Database, Video, Compilation, compilation_videos

console = Console()


def load_config(config_path="config/config.yaml"):
    if not os.path.exists(config_path):
        console.print(f"[red]Error: {config_path} not found[/red]")
        exit(1)
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def select_videos(session, topic, count, cooldown_days):
    # Get all videos for topic
    all_videos = session.query(Video).filter(Video.topic == topic).order_by(
        Video.upload_date.desc()
    ).all()
    
    if not all_videos:
        return []
    
    # Filter by cooldown
    cooldown_date = datetime.utcnow() - timedelta(days=cooldown_days)
    
    available = []
    for video in all_videos:
        # Check if used recently
        recent_use = session.query(compilation_videos).join(Compilation).filter(
            compilation_videos.c.video_id == video.id,
            Compilation.created_at >= cooldown_date
        ).first()
        
        if not recent_use:
            available.append(video)
    
    # If not enough, use older ones too
    if len(available) < count:
        used = [v for v in all_videos if v not in available]
        available.extend(used[:count - len(available)])
    
    return available[:count]


def download_video(video, download_path, yt_format, retry_attempts=3):
    # Check if already downloaded
    for file in Path(download_path).glob("*.mp4"):
        if video.youtube_id in file.name:
            return file
    
    # Download with retry
    for attempt in range(retry_attempts):
        try:
            output_template = str(Path(download_path) / "%(title).150s_[%(id)s].%(ext)s")
            
            cmd = [
                "yt-dlp",
                "-f", yt_format,
                "--output", output_template,
                "--restrict-filenames",
                "--no-warnings",
                # Anti-403 options
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "--referer", "https://www.youtube.com/",
                "--extractor-args", "youtube:player_client=android,web",
                video.url
            ]
            
            subprocess.run(cmd, capture_output=True, timeout=1800, check=True)
            
            # Find downloaded file
            for file in Path(download_path).glob("*.mp4"):
                if video.youtube_id in file.name:
                    return file
            
        except Exception as e:
            if attempt < retry_attempts - 1:
                console.print(f"[yellow]Download attempt {attempt + 1}/{retry_attempts} failed, retrying...[/yellow]")
                continue
            else:
                error_str = str(e)
                if "403" in error_str or "Forbidden" in error_str:
                    console.print(f"[yellow]Failed (403 - update yt-dlp!): {video.title[:50]}[/yellow]")
                else:
                    console.print(f"[yellow]Failed to download: {video.title[:50]}[/yellow]")
                return None
    
    return None


def download_videos_parallel(videos, download_path, yt_format, max_workers=3):
    downloaded = {}
    
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        
        task = progress.add_task("[cyan]Downloading videos...", total=len(videos))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_video = {
                executor.submit(download_video, v, download_path, yt_format): v
                for v in videos
            }
            
            for future in as_completed(future_to_video):
                video = future_to_video[future]
                try:
                    filepath = future.result()
                    if filepath:
                        downloaded[video.id] = filepath
                except Exception:
                    pass
                progress.update(task, advance=1)
    
    return downloaded


def compile_videos(video_files, topic, output_path):
    if not video_files:
        console.print("[red]No videos to compile[/red]")
        return None
    
    # Create concat file
    concat_file = Path(output_path) / "concat_list.txt"
    with open(concat_file, 'w', encoding='utf-8') as f:
        for filepath in video_files.values():
            f.write(f"file '{filepath.absolute()}'\n")
    
    # Output filename
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = f"{topic}_compilation_{timestamp}.mp4"
    output_filepath = Path(output_path) / output_filename
    
    console.print(f"\n[cyan]Compiling {len(video_files)} videos...[/cyan]")
    
    # Try fast concat first (no re-encode)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_filepath)
    ]
    
    result = subprocess.run(cmd, capture_output=True)
    
    if result.returncode == 0 and output_filepath.exists():
        console.print(f"[green]✓ Fast compilation successful[/green]")
        return output_filepath
    
    # Fallback: re-encode
    console.print("[yellow]Fast concat failed, re-encoding...[/yellow]")
    
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_filepath)
    ]
    
    result = subprocess.run(cmd, capture_output=True, timeout=3600)
    
    if result.returncode == 0 and output_filepath.exists():
        console.print(f"[green]✓ Re-encoded compilation successful[/green]")
        return output_filepath
    
    console.print("[red]✗ Compilation failed[/red]")
    return None


def cleanup_downloads(download_path, keep_files=None):
    keep_set = set(keep_files) if keep_files else set()
    removed = 0
    
    for file in Path(download_path).glob("*.mp4"):
        if file not in keep_set:
            try:
                file.unlink()
                removed += 1
            except:
                pass
    
    if removed > 0:
        console.print(f"[dim]Cleaned up {removed} files[/dim]")


def main():
    console.print("\n[bold cyan]🎬 Creating Video Compilation[/bold cyan]\n")
    
    # Load config
    cfg = load_config()
    
    # Connect to database
    db = Database(cfg['db_path'])
    session = db.get_session()
    
    # Create directories
    os.makedirs(cfg['download_path'], exist_ok=True)
    os.makedirs(cfg['output_path'], exist_ok=True)
    
    # Get user input
    topics = list(cfg['topics'].keys())
    console.print(f"[cyan]Available topics:[/cyan] {', '.join(topics)}")
    
    topic = input("\nEnter topic: ").strip().lower()
    if topic not in topics:
        console.print(f"[red]Error: '{topic}' not in available topics[/red]")
        exit(1)
    
    try:
        count = int(input("How many videos? (default: 10): ").strip() or "10")
    except:
        count = 10
    
    # Select videos
    console.print(f"\n[cyan]Selecting videos...[/cyan]")
    videos = select_videos(session, topic, count, cfg.get('cooldown_days', 30))
    
    if not videos:
        console.print(f"[yellow]No videos found for topic '{topic}'[/yellow]")
        exit(0)
    
    console.print(f"[green]✓ Selected {len(videos)} videos[/green]\n")
    
    # Download videos
    max_workers = cfg.get('max_concurrent_downloads', 3)
    video_files = download_videos_parallel(
        videos, 
        cfg['download_path'], 
        cfg['yt_dlp_format'],
        max_workers
    )
    
    if not video_files:
        console.print("[red]No videos downloaded successfully[/red]")
        console.print("[yellow]💡 Tip: Try updating yt-dlp: pip install -U yt-dlp[/yellow]")
        exit(1)
    
    console.print(f"\n[green]✓ Downloaded {len(video_files)}/{len(videos)} videos[/green]")
    
    # Compile videos
    output_file = compile_videos(video_files, topic, cfg['output_path'])
    
    if not output_file:
        exit(1)
    
    # Save to database
    compilation = Compilation(
        topic=topic,
        filename=output_file.name,
        video_count=len(video_files)
    )
    session.add(compilation)
    session.flush()
    
    # Link videos to compilation
    for video_id in video_files.keys():
        stmt = compilation_videos.insert().values(
            compilation_id=compilation.id,
            video_id=video_id
        )
        session.execute(stmt)
    
    session.commit()
    session.close()
    
    # Cleanup
    cleanup_downloads(cfg['download_path'], list(video_files.values()))
    
    console.print(f"\n[green bold]✅ Done![/green bold]")
    console.print(f"[green]Compilation saved: {output_file}[/green]\n")


if __name__ == "__main__":
    main()