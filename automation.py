#!/usr/bin/env python3
"""
SDMNH Automation — fully hands-off compile + upload pipeline.

Designed to run once per day via cron or systemd timer.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    wait_and_delete_when_public,
)

# ── State files ───────────────────────────────────────────────────────────────
LAST_RUN_FILE = BASE_DIR / 'data' / 'last_run.json'
LAST_DB_UPDATE_FILE = BASE_DIR / 'data' / 'last_db_update.json'
LOG_FILE = BASE_DIR / 'data' / 'automation.log'
DB_UPDATE_INTERVAL_DAYS = 7   # Re-scrape YouTube channels every 7 days
NETWORK_WAIT_SECONDS = 120    # Wait up to 2 min for network on startup
MAX_RUNS_PER_DAY = 2          # Two uploads per day (morning + evening)
MAX_RUN_SECONDS = 7200        # Hard 2-hour cap per run — kills the process if exceeded


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging():
    from logging.handlers import RotatingFileHandler
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler])


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logging.warning("Failed to read %s: %s", path, exc)
    return {}


def _save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def _today_utc() -> str:
    """Return today's UTC date as an ISO string (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).date().isoformat()


def _today_state() -> dict:
    """Return today's last_run.json entry, or a fresh skeleton if absent/stale."""
    data = _load_json(LAST_RUN_FILE)
    if data.get('date') == _today_utc():
        return data
    return {'date': _today_utc(), 'runs': [], 'failed_topics': []}


def already_ran_today() -> bool:
    """True when MAX_RUNS_PER_DAY successful uploads have already completed today."""
    state = _today_state()
    successful = [r for r in state.get('runs', []) if r.get('video_id')]
    return len(successful) >= MAX_RUNS_PER_DAY


def get_todays_failed_topics() -> list:
    """Return topics that already failed compilation today (should not be retried)."""
    return _today_state().get('failed_topics', [])


def get_todays_used_topics() -> list:
    """Return topics that already completed a successful upload today."""
    state = _today_state()
    return [r['topic'] for r in state.get('runs', []) if r.get('video_id')]


def record_failed_topic(topic: str):
    """Append topic to today's failed list so it won't be picked again today."""
    state = _today_state()
    failed = state.get('failed_topics', [])
    if topic not in failed:
        failed.append(topic)
    state['failed_topics'] = failed
    _save_json(LAST_RUN_FILE, state)


def record_run(topic: str, title: str, video_id: str | None, duration_seconds: float):
    state = _today_state()
    runs = state.get('runs', [])
    runs.append({
        'topic': topic,
        'title': title,
        'video_id': video_id,
        'duration_seconds': round(duration_seconds),
        'youtube_url': f'https://www.youtube.com/watch?v={video_id}' if video_id else None,
    })
    state['runs'] = runs
    _save_json(LAST_RUN_FILE, state)


def db_needs_update() -> bool:
    data = _load_json(LAST_DB_UPDATE_FILE)
    if not data.get('date'):
        return True
    try:
        last = datetime.fromisoformat(data['date'])
        return (datetime.now(timezone.utc) - last).days >= DB_UPDATE_INTERVAL_DAYS
    except Exception:
        return True


def record_db_update():
    _save_json(LAST_DB_UPDATE_FILE, {'date': datetime.now(timezone.utc).isoformat()})


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
    )
    if result.returncode == 0:
        logging.info("Database refresh complete.")
        record_db_update()
    else:
        logging.warning("Database refresh finished with errors — continuing anyway.")


# ── Topic selection ───────────────────────────────────────────────────────────

def select_topic_by_rank(
    session,
    topics_config: dict,
    skip_topics: list | None = None,
    max_uses_per_week: int = 2,
) -> str | None:
    """
    Pick the highest-ranked topic that:
      - Has at least one video in the database
      - Has not been used >= max_uses_per_week times in the current calendar week
      - Is not in skip_topics (e.g. topics that already failed today)

    Topics are ranked by the total view_count of all their videos (descending).
    Falls back to 'general' if all specific topics are exhausted.
    """
    from sqlalchemy import func
    from src.database import Compilation

    skip = set(skip_topics or [])

    # Count how many times each topic was compiled this calendar week (Mon–Sun)
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_start_dt = datetime(
        week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc
    )
    weekly_counts: dict[str, int] = {}
    rows = (
        session.query(Compilation.topic, func.count(Compilation.id))
        .filter(Compilation.created_at >= week_start_dt)
        .group_by(Compilation.topic)
        .all()
    )
    for t, cnt in rows:
        weekly_counts[t] = cnt

    # Score every eligible topic by aggregate view_count
    topic_scores: list[tuple[str, int]] = []
    for topic in topics_config:
        if topic == 'general' or topic in skip:
            continue
        if weekly_counts.get(topic, 0) >= max_uses_per_week:
            logging.debug("Topic '%s' skipped — used %d/%d times this week",
                          topic, weekly_counts[topic], max_uses_per_week)
            continue
        video_count = session.query(Video).filter(Video.topic == topic).count()
        if video_count == 0:
            continue
        total_views: int = (
            session.query(func.sum(Video.view_count))
            .filter(Video.topic == topic, Video.view_count.isnot(None))
            .scalar()
        ) or 0
        topic_scores.append((topic, total_views))

    if topic_scores:
        topic_scores.sort(key=lambda x: x[1], reverse=True)
        chosen, chosen_views = topic_scores[0]
        skipped_msg = f", skipped today: {sorted(skip)}" if skip else ""
        logging.info(
            "Selected topic '%s' (rank 1 of %d eligible, %s total views%s)",
            chosen, len(topic_scores), f"{chosen_views:,}", skipped_msg,
        )
        return chosen

    # All specific topics exhausted/capped — fall back to general
    general_count = session.query(Video).filter(Video.topic == 'general').count()
    if general_count > 0 and 'general' not in skip:
        logging.info(
            "All specific topics exhausted/weekly-capped — falling back to 'general' "
            "(%d videos available)", general_count,
        )
        return 'general'

    logging.error(
        "No topics have videos. Run:  python update_db.py  to populate the database."
    )
    return None


# Keep old name as alias so existing tests and call-sites still work
def select_random_topic(
    session,
    topics_config: dict,
    skip_topics: list | None = None,
) -> str | None:
    return select_topic_by_rank(session, topics_config, skip_topics=skip_topics)


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


# ── Watchdog ──────────────────────────────────────────────────────────────────

def _install_watchdog(max_seconds: int = MAX_RUN_SECONDS):
    """Fire a watchdog thread that SIGKILLs the process if it runs too long."""
    import threading
    import signal

    def _kill():
        logging.error("WATCHDOG: run exceeded %d seconds — killing process tree", max_seconds)
        try:
            os.killpg(os.getpgid(os.getpid()), signal.SIGKILL)
        except Exception:
            os._exit(124)

    t = threading.Timer(max_seconds, _kill)
    t.daemon = True
    t.start()


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
    parser.add_argument(
        '--ephemeral', action='store_true',
        help='Ephemeral mode: skip network wait and YouTube processing wait',
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
    logging.info('SDMNH Automation starting (hard cap: %ds)', MAX_RUN_SECONDS)
    _install_watchdog(MAX_RUN_SECONDS)

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

    # ── Wait for network (skip on ephemeral — DO droplets always have network) ──
    if not args.ephemeral:
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
    topic = resolve_topic(cfg, args)
    if not topic:
        return

    # ── Run pipeline ──
    run_pipeline(cfg, topic, ephemeral=args.ephemeral)


def resolve_topic(cfg: dict, args) -> str | None:
    """Select a topic from args or randomly from the database."""
    db = Database(cfg['db_path'])
    with db.session_scope() as session:
        if args.topic:
            if args.topic not in cfg['topics']:
                logging.error(f"Unknown topic '{args.topic}'. Check config.yaml for valid options.")
                return None
            count = session.query(Video).filter(Video.topic == args.topic).count()
            if count == 0:
                logging.error(
                    f"Topic '{args.topic}' has no videos in the database. "
                    "Run update_db.py or choose a different topic."
                )
                return None
            logging.info(f"Using manually specified topic: {args.topic}")
            return args.topic

        failed_today = get_todays_failed_topics()
        used_today = get_todays_used_topics()
        skip_today = list(set(failed_today + used_today))
        if skip_today:
            logging.info("Skipping topics already used/failed today: %s", skip_today)
        return select_topic_by_rank(
            session,
            cfg['topics'],
            skip_topics=skip_today,
            max_uses_per_week=cfg.get('max_topic_uses_per_week', 2),
        )


def run_pipeline(cfg: dict, topic: str, ephemeral: bool = False):
    """Compile videos, upload to YouTube, and clean up."""
    # Mark today as started (preserve existing runs + failed_topics)
    state = _today_state()
    state['status'] = 'in_progress'
    _save_json(LAST_RUN_FILE, state)

    # ── Compile ──
    max_hours = cfg.get('max_compilation_hours', 12)
    logging.info(f"Starting compilation — topic: '{topic}', max duration: {max_hours}h")

    try:
        output_file, total_seconds, selected_videos = run_auto(
            topic=topic, max_hours=max_hours, cfg=cfg,
        )
    except Exception as exc:
        logging.error(f"Compilation failed: {exc}")
        record_failed_topic(topic)
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
    service = None
    try:
        service = authenticate(yt_cfg['credentials_path'], yt_cfg['token_path'])
        video_id = upload_video(
            service=service, video_path=output_file, title=title,
            description=description, tags=tags,
            category_id=category_id, privacy_status=privacy_status,
        )
        logging.info(f"Upload successful!  video_id={video_id}")
        logging.info(f"YouTube URL: https://www.youtube.com/watch?v={video_id}")

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

    # ── Record run ──
    record_run(topic, title, video_id, total_seconds)

    # ── Delete output file once YouTube confirms the video is public ──
    # In ephemeral mode, droplet destruction handles cleanup — skip the wait.
    if ephemeral:
        logging.info("Ephemeral mode — skipping YouTube processing wait (droplet cleanup handles files).")
    elif video_id and service:
        max_wait = int(cfg.get('youtube_processing_wait_seconds', 14400))
        deleted = wait_and_delete_when_public(
            service=service, video_id=video_id, video_path=output_file,
            poll_interval=60, max_wait_seconds=max_wait, log_fn=logging.info,
        )
        if not deleted:
            logging.warning(
                f"Output file was NOT deleted (timed out or upload failed). "
                f"You can delete it manually: {output_file}"
            )
    else:
        logging.info(f"Upload did not complete — output file retained: {output_file.name}")

    logging.info('Automation complete.')
    logging.info('=' * 56)


if __name__ == '__main__':
    main()
