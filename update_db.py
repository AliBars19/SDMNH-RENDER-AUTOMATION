import os
import json
import yaml
import subprocess
from datetime import datetime
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

from src.database import Database, Video

console = Console()


def load_config(config_path="config/config.yaml"):
    if not os.path.exists(config_path):
        console.print(f"[red]Error: {config_path} not found[/red]")
        exit(1)
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)
    
    # Validate required keys
    required = ['channels', 'download_path', 'output_path', 'db_path', 'topics', 'yt_dlp_format']
    missing = [k for k in required if k not in cfg]
    if missing:
        console.print(f"[red]Error: Missing config keys: {missing}[/red]")
        exit(1)
    
    return cfg


def scrape_channel(channel_url):
    cmd = ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", channel_url]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        console.print(f"[yellow]Warning: Timeout scraping {channel_url}[/yellow]")
        return []
    
    videos = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        
        try:
            vid_info = json.loads(line)
            
            # Skip shorts
            url = vid_info.get("url", "") or vid_info.get("webpage_url", "")
            if "shorts" in url.lower():
                continue
            
            # Filter by year (only 2020+)
            upload_date = vid_info.get("upload_date")
            if upload_date:
                year = int(str(upload_date)[:4])
                if year < 2020:
                    continue
            
            videos.append({
                "youtube_id": vid_info.get("id"),
                "title": vid_info.get("title"),
                "url": vid_info.get("webpage_url") or vid_info.get("url"),
                "duration": vid_info.get("duration"),
                "upload_date": upload_date,
                "channel": vid_info.get("channel") or vid_info.get("uploader")
            })
        except:
            continue
    
    return videos


def assign_topic(title, topics_config):
    if not title:
        return "general"
    
    title_lower = title.lower()
    
    for topic_name, keywords in topics_config.items():
        if topic_name == "general":
            continue
        for keyword in keywords:
            if keyword.lower() in title_lower:
                return topic_name
    
    return "general"


def main():
    console.print("\n[bold cyan] Updating Video Database[/bold cyan]\n")
    
    # Load config
    cfg = load_config()
    
    # Connect to database
    db = Database(cfg['db_path'])
    session = db.get_session()
    
    all_videos = []
    
    # Scrape each channel with progress
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        
        for channel_url in cfg['channels']:
            task = progress.add_task(f"Scraping {channel_url}...", total=None)
            videos = scrape_channel(channel_url)
            all_videos.extend(videos)
            progress.update(task, description=f" Found {len(videos)} videos", completed=True)
    
    console.print(f"\n[green]Found {len(all_videos)} total videos[/green]")
    
    # Save to database
    new_count = 0
    
    for vid_data in all_videos:
        youtube_id = vid_data.get("youtube_id")
        if not youtube_id:
            continue
        
        # Check if exists
        existing = session.query(Video).filter_by(youtube_id=youtube_id).first()
        if existing:
            continue
        
        # Assign topic
        topic = assign_topic(vid_data.get("title"), cfg['topics'])
        
        # Create new video
        video = Video(
            youtube_id=youtube_id,
            title=vid_data.get("title"),
            url=vid_data.get("url"),
            duration=vid_data.get("duration"),
            upload_date=vid_data.get("upload_date"),
            channel=vid_data.get("channel"),
            topic=topic
        )
        
        session.add(video)
        new_count += 1
    
    session.commit()
    session.close()
    
    total = session.query(Video).count()
    console.print(f"\n[green] Added {new_count} new videos[/green]")
    console.print(f"[green] Total videos in database: {total}[/green]\n")


if __name__ == "__main__":
    main()