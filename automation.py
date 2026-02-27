#!/usr/bin/env python3
"""
SDMNH Automation — fully hands-off compile + upload pipeline.

Designed to run once per day when the laptop starts via Windows Task Scheduler.
See setup_startup.ps1 to register the scheduled task.

Usage
-----
  python automation.py           # Normal run (skips if already ran today)
  python automation.py --force   # Run even if already ran today
  python automation.py --topic try_not_to_laugh   # Override random topic
  python automation.py --setup   # First-time YouTube OAuth setup (run once manually)
  python automation.py --update-db  # Force database refresh and exit

What it does each run
---------------------
  1. Wait up to 2 minutes for a network connection.
  2. Refresh the video database if it hasn't been updated in 7 days.
  3. Pick a random topic that has videos in the database.
  4. Select videos whose TOTAL duration stays under the 12-hour cap
     (checked using DB duration values — nothing is downloaded first).
  5. Download, compile, and clean up temporary files.
  6. Extract a thumbnail frame from the compiled video.
  7. Upload to YouTube with auto-generated title and description.
  8. Set the thumbnail.
  9. Record the run so it won't repeat today.
"""

import argparse
import json
import logging
import os
import random
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from combine import load_config, run_auto
from src.database import Database, Video
from src.youtube_upload import (
    authenticate,
    build_tags,
    extract_thumbnail,
    format_description,
    format_title,
    set_thumbnail,
    upload_video,
)

# ── State files ───────────────────────────────────────────────────────────────
LAST_RUN_FILE = BASE_DIR / 'data' / 'last_run.json'
LAST_DB_UPDATE_FILE = BASE_DIR / 'data' / 'last_db_update.json'
LOG_FILE = BASE_DIR / 'data' / 'automation.log'
DB_UPDATE_INTERVAL_DAYS = 7   # Re-scrape YouTube channels every 7 days
NETWORK_WAIT_SECONDS = 120    # Wait up to 2 min for network on startup


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def already_ran_today() -> bool:
    data = _load_json(LAST_RUN_FILE)
    if data.get('date') != datetime.utcnow().date().isoformat():
        return False
    # Allow a retry if today's run never produced a successful upload
    return data.get('video_id') is not None


def record_run(topic: str, title: str, video_id: str | None, duration_seconds: float):
    _save_json(LAST_RUN_FILE, {
        'date': datetime.utcnow().date().isoformat(),
        'topic': topic,
        'title': title,
        'video_id': video_id,
        'duration_seconds': round(duration_seconds),
        'youtube_url': f'https://www.youtube.com/watch?v={video_id}' if video_id else None,
    })


def db_needs_update() -> bool:
    data = _load_json(LAST_DB_UPDATE_FILE)
    if not data.get('date'):
        return True
    try:
        last = datetime.fromisoformat(data['date'])
        return (datetime.utcnow() - last).days >= DB_UPDATE_INTERVAL_DAYS
    except Exception:
        return True


def record_db_update():
    _save_json(LAST_DB_UPDATE_FILE, {'date': datetime.utcnow().isoformat()})


# ── Network ───────────────────────────────────────────────────────────────────

def wait_for_network(max_seconds: int = NETWORK_WAIT_SECONDS) -> bool:
    """Block until a TCP connection to Google DNS succeeds or timeout expires."""
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(('8.8.8.8', 53))
            s.close()
            return True
        except (socket.error, OSError):
            time.sleep(5)
    return False


# ── Database update ───────────────────────────────────────────────────────────

def update_database():
    """Run update_db.py as a subprocess to refresh video metadata."""
    logging.info("Refreshing video database from YouTube channels...")
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / 'update_db.py')],
        cwd=str(BASE_DIR),
        timeout=600,
        creationflags=_NO_WINDOW,
    )
    if result.returncode == 0:
        logging.info("Database refresh complete.")
        record_db_update()
    else:
        logging.warning("Database refresh finished with errors — continuing anyway.")


# ── Topic selection ───────────────────────────────────────────────────────────

def select_random_topic(session, topics_config: dict) -> str | None:
    """
    Pick a random topic that has at least one video in the database.
    Excludes 'general' from automated selection (too broad).
    """
    candidates = []
    for topic in topics_config:
        if topic == 'general':
            continue
        count = session.query(Video).filter(Video.topic == topic).count()
        if count > 0:
            candidates.append(topic)

    if not candidates:
        logging.error(
            "No topics have videos. Run:  python update_db.py  to populate the database."
        )
        return None

    chosen = random.choice(candidates)
    logging.info(
        f"Randomly selected topic '{chosen}' "
        f"(pool: {len(candidates)} topics with videos)"
    )
    return chosen


# ── First-time setup ──────────────────────────────────────────────────────────

def run_setup(cfg: dict):
    print("\n" + "=" * 56)
    print("  SDMNH First-Time YouTube Setup")
    print("=" * 56)
    print()
    print("Before continuing, make sure you have:")
    print()
    print("  1. A Google Cloud project at:")
    print("     https://console.cloud.google.com/")
    print()
    print("  2. YouTube Data API v3 enabled for that project")
    print()
    print("  3. OAuth 2.0 credentials (Desktop application) downloaded")
    print(f"     and saved to:  {cfg['youtube']['credentials_path']}")
    print()
    print("  4. Your YouTube channel verified (for thumbnail uploads):")
    print("     https://www.youtube.com/verify")
    print()
    input("Press Enter to open the browser and authorise the app...")

    service = authenticate(
        cfg['youtube']['credentials_path'],
        cfg['youtube']['token_path'],
    )
    print()
    print("✅  Authentication successful!")
    print(f"    Token saved to: {cfg['youtube']['token_path']}")
    print()
    print("You can now run:  python automation.py")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='SDMNH — automated Sidemen compilation pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--setup', action='store_true',
        help='Run first-time YouTube OAuth setup and exit',
    )
    parser.add_argument(
        '--force', action='store_true',
        help="Ignore the 'already ran today' guard",
    )
    parser.add_argument(
        '--topic', type=str, default=None,
        help='Override automatic topic selection (e.g. --topic try_not_to_laugh)',
    )
    parser.add_argument(
        '--update-db', action='store_true',
        help='Force a database refresh and exit',
    )
    args = parser.parse_args()

    os.chdir(BASE_DIR)
    setup_logging()

    cfg = load_config()

    # ── First-time setup mode ──
    if args.setup:
        run_setup(cfg)
        return

    logging.info('=' * 56)
    logging.info('SDMNH Automation starting')

    # ── Force database update ──
    if args.update_db:
        update_database()
        logging.info('Database update complete — exiting.')
        return

    # ── Once-per-day guard ──
    if not args.force and already_ran_today():
        logging.info(
            "Already ran today. Use --force to override, or wait until tomorrow."
        )
        return

    # ── Wait for network ──
    logging.info(f"Waiting for network (up to {NETWORK_WAIT_SECONDS}s)...")
    if not wait_for_network():
        logging.error("No network available — cannot proceed. Will retry on next startup.")
        return
    logging.info("Network connection established.")

    # ── Refresh database if stale ──
    if db_needs_update():
        update_database()
    else:
        logging.info("Database is up to date (refreshed within the last 7 days).")

    # ── Select topic ──
    db = Database(cfg['db_path'])
    session = db.get_session()

    topic = args.topic
    if topic:
        if topic not in cfg['topics']:
            logging.error(f"Unknown topic '{topic}'. Check config.yaml for valid options.")
            session.close()
            return
        # Validate it has videos
        count = session.query(Video).filter(Video.topic == topic).count()
        if count == 0:
            logging.error(
                f"Topic '{topic}' has no videos in the database. "
                "Run update_db.py or choose a different topic."
            )
            session.close()
            return
        logging.info(f"Using manually specified topic: {topic}")
    else:
        topic = select_random_topic(session, cfg['topics'])
        if not topic:
            session.close()
            return

    session.close()

    # ── Mark today as started (prevents re-runs on restart even if something fails) ──
    _save_json(LAST_RUN_FILE, {'date': datetime.utcnow().date().isoformat(), 'status': 'in_progress'})

    # ── Compile ──
    max_hours = cfg.get('max_compilation_hours', 12)
    logging.info(f"Starting compilation — topic: '{topic}', max duration: {max_hours}h")

    try:
        output_file, total_seconds, selected_videos = run_auto(
            topic=topic,
            max_hours=max_hours,
            cfg=cfg,
        )
    except Exception as exc:
        logging.error(f"Compilation failed: {exc}")
        return

    hours = total_seconds / 3600
    logging.info(f"Compilation done: {output_file.name}  ({hours:.2f}h / {total_seconds:.0f}s)")

    # ── Thumbnail ──
    thumb_dir = Path(cfg.get('thumbnail_path', 'data/thumbnails'))
    thumb_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    thumbnail_path = str(thumb_dir / f'thumb_{timestamp}.jpg')

    thumb = extract_thumbnail(selected_videos, thumbnail_path)
    if thumb:
        logging.info(f"Thumbnail extracted: {thumb}")
    else:
        logging.warning("Could not extract thumbnail — upload will proceed without one.")

    # ── Build YouTube metadata ──
    title = format_title(topic, total_seconds)
    yt_cfg = cfg.get('youtube', {})
    description = format_description(topic, yt_cfg.get('description', ''))
    tags = build_tags(topic, yt_cfg.get('tags', []))
    category_id = str(yt_cfg.get('category_id', '24'))
    privacy_status = yt_cfg.get('privacy_status', 'public')

    logging.info(f"YouTube title: {title}")
    logging.info(f"Privacy: {privacy_status}")

    # ── Upload ──
    video_id = None
    try:
        service = authenticate(
            yt_cfg['credentials_path'],
            yt_cfg['token_path'],
        )

        video_id = upload_video(
            service=service,
            video_path=output_file,
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy_status=privacy_status,
        )

        logging.info(f"Upload successful!  video_id={video_id}")
        logging.info(f"YouTube URL: https://www.youtube.com/watch?v={video_id}")

        # Set thumbnail
        if thumb:
            ok = set_thumbnail(service, video_id, thumb)
            if ok:
                logging.info("Thumbnail set successfully.")
            else:
                logging.warning(
                    "Thumbnail upload failed. Make sure your channel is verified at "
                    "https://www.youtube.com/verify"
                )

    except FileNotFoundError as exc:
        logging.error(str(exc))
        logging.error("Run:  python automation.py --setup  to authenticate.")
    except Exception as exc:
        logging.error(f"YouTube upload failed: {exc}")

    # ── Record run (even if upload failed, compilation is saved) ──
    record_run(topic, title, video_id, total_seconds)

    logging.info('Automation complete.')
    logging.info('=' * 56)


if __name__ == '__main__':
    main()
