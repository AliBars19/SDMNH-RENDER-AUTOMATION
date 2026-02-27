"""
YouTube Data API v3 upload module for SDMNH Automation.

First-time setup
----------------
1. Go to https://console.cloud.google.com/
2. Create a project (or use an existing one)
3. Enable the "YouTube Data API v3"
4. Create OAuth 2.0 credentials → Desktop application
5. Download the JSON and save it as:   credentials/client_secrets.json
6. Run:  python automation.py --setup
   A browser window will open for you to authorise the app.
   Your token is then cached in credentials/youtube_token.json for future runs.

NOTE: To upload custom thumbnails your YouTube channel must be verified
(phone verification at youtube.com/verify).
"""

import os
import subprocess
import urllib.request
from pathlib import Path

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

import time

# Scopes required: upload videos + manage thumbnails
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube',
]

# Human-readable display names for each topic key
TOPIC_DISPLAY_NAMES = {
    'among_us':             'AMONG US',
    'try_not_to_laugh':     'TRY NOT TO LAUGH',
    'the_price_is_right':   'THE PRICE IS RIGHT',
    'mukbang':              'MUKBANG',
    'five_second_challenge':'5 SECOND CHALLENGE',
    'hide_and_seek':        'HIDE AND SEEK',
    'mafia':                'MAFIA',
    'guess_the_link':       'GUESS THE LINK',
    'guess_the_lyric':      'GUESS THE LYRIC',
    'guessmoji':            'GUESSMOJI',
    'sidemen_sunday':       'SIDEMEN SUNDAY',
    'holiday':              'HOLIDAY',
    'road_trip':            'ROAD TRIP',
    'cooking':              'COOKING CHALLENGE',
    'dating':               'DATING',
    'football':             'FOOTBALL',
    'quiz':                 'QUIZ',
    'charity':              'CHARITY',
    'would_you_rather':     'WOULD YOU RATHER',
    'fashion':              'FASHION',
    'ultimate':             'ULTIMATE',
    'tasting':              'TASTING',
    'general':              'COMPILATION',
}

# Extra topic-specific tags appended on top of the base tag list
TOPIC_TAGS = {
    'among_us':             ['among us', 'among us sidemen'],
    'try_not_to_laugh':     ['try not to laugh', 'tntl', 'comedy'],
    'the_price_is_right':   ['price is right', 'game show', 'sidemen game'],
    'mukbang':              ['mukbang', 'eating', 'food'],
    'five_second_challenge':['5 second challenge', 'challenge'],
    'hide_and_seek':        ['hide and seek', 'challenge'],
    'mafia':                ['mafia', 'social deduction'],
    'guess_the_link':       ['guess the link', 'sidemen game'],
    'guess_the_lyric':      ['guess the lyric', 'music challenge'],
    'guessmoji':            ['guessmoji', 'emoji challenge'],
    'sidemen_sunday':       ['sidemen sunday', 'weekly'],
    'holiday':              ['holiday', 'vacation', 'travel', 'vlog'],
    'road_trip':            ['road trip', 'travel', 'vlog'],
    'cooking':              ['cooking', 'food challenge', 'masterchef'],
    'dating':               ['dating', 'tinder', 'love', 'romance'],
    'football':             ['football', 'soccer', 'sidemen fc'],
    'quiz':                 ['quiz', 'trivia', 'knowledge'],
    'charity':              ['charity', 'fundraiser', 'good cause'],
    'would_you_rather':     ['would you rather', 'wyr'],
    'fashion':              ['fashion', 'clothing', 'outfit'],
    'ultimate':             ['ultimate', 'extreme'],
    'tasting':              ['tasting', 'taste test', 'food'],
}


# ── Authentication ─────────────────────────────────────────────────────────────

def authenticate(credentials_path: str, token_path: str):
    """
    Authenticate with the YouTube Data API using OAuth 2.0.

    On first call a browser window opens for user consent.
    Subsequent calls reuse the cached token from token_path.

    Returns a googleapiclient Resource object ready to make API calls.
    """
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"YouTube credentials file not found: {credentials_path}\n"
                    "Please follow the setup instructions at the top of src/youtube_upload.py "
                    "and run:  python automation.py --setup"
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Persist token so next run doesn't need a browser
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    return build('youtube', 'v3', credentials=creds)


# ── Title & metadata helpers ───────────────────────────────────────────────────

def format_title(topic: str, duration_seconds: float) -> str:
    """
    Return the YouTube video title.
    Format: SIDEMEN {TOPIC} - X HOUR SPECIAL

    Hours are rounded to the nearest integer (minimum 1).
    """
    topic_display = TOPIC_DISPLAY_NAMES.get(topic, topic.replace('_', ' ').upper())
    hours = max(1, round(duration_seconds / 3600))
    return f"SIDEMEN {topic_display} - {hours} HOUR SPECIAL"


def format_description(topic: str, description_template: str) -> str:
    """Fill in the description template with topic-specific values."""
    topic_display = TOPIC_DISPLAY_NAMES.get(topic, topic.replace('_', ' ').title())
    topic_tag = topic.replace('_', '').replace(' ', '')
    return description_template.format(topic=topic_display, topic_tag=topic_tag)


def build_tags(topic: str, base_tags: list) -> list:
    """Combine base tags from config with topic-specific tags. Max 500 tags."""
    topic_word = topic.replace('_', ' ')
    extra = TOPIC_TAGS.get(topic, [topic_word])

    all_tags = list(base_tags)
    for tag in extra:
        if tag not in all_tags:
            all_tags.append(tag)

    # Resolve any {topic_tag} placeholder in base tags
    all_tags = [t.replace('{topic_tag}', topic_word) for t in all_tags]
    return all_tags[:500]


# ── Thumbnail ──────────────────────────────────────────────────────────────────

def extract_thumbnail(youtube_ids: list, output_path: str) -> str | None:
    """
    Download the actual YouTube thumbnail from one of the source videos.

    Tries maxresdefault (1280x720) then hqdefault (480x360) for each video ID
    in order, stopping at the first usable image (> 5 KB to skip placeholders).
    Returns output_path on success, None if all attempts fail.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    for video_id in youtube_ids:
        for quality in ('maxresdefault', 'sddefault', 'hqdefault'):
            url = f'https://img.youtube.com/vi/{video_id}/{quality}.jpg'
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = resp.read()
                # Skip tiny placeholder images — real thumbnails are at least 5 KB
                if len(data) < 5000:
                    continue
                with open(output_path, 'wb') as f:
                    f.write(data)
                return output_path
            except Exception:
                continue

    return None


# ── Upload ─────────────────────────────────────────────────────────────────────

def upload_video(
    service,
    video_path,
    title: str,
    description: str,
    tags: list,
    category_id: str = '24',
    privacy_status: str = 'public',
) -> str:
    """
    Upload a video to YouTube using the resumable upload protocol.

    Automatically retries on transient server errors (5xx).
    Returns the YouTube video_id string on success.
    """
    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id,
            'defaultLanguage': 'en',
            'defaultAudioLanguage': 'en',
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False,
            'madeForKids': False,
        },
    }

    # 50 MB chunks — keeps memory usage low for multi-hour files
    media = MediaFileUpload(
        str(video_path),
        mimetype='video/mp4',
        resumable=True,
        chunksize=50 * 1024 * 1024,
    )

    insert_request = service.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media,
    )

    print(f"\n  Uploading: {title}")
    print(f"  File:      {Path(video_path).name}")
    size_gb = Path(video_path).stat().st_size / (1024 ** 3)
    print(f"  Size:      {size_gb:.2f} GB")

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = insert_request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"\r  Progress: {pct}%  ", end='', flush=True)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                retry_count += 1
                if retry_count > 10:
                    raise Exception(f"Upload failed after {retry_count} retries: {e}") from e
                wait = min(2 ** retry_count, 64)
                print(f"\n  Server error {e.resp.status} — retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

    print()  # newline after progress bar
    return response['id']


def set_thumbnail(service, video_id: str, thumbnail_path: str) -> bool:
    """
    Set the thumbnail for a YouTube video.

    Requires the channel to be verified (phone verification at youtube.com/verify).
    Returns True on success, False on failure (logged but not raised).
    """
    try:
        service.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path),
        ).execute()
        return True
    except HttpError as e:
        print(f"  Warning: thumbnail upload failed: {e}")
        return False


def wait_and_delete_when_public(
    service,
    video_id: str,
    video_path,
    poll_interval: int = 60,
    max_wait_seconds: int = 7200,
    log_fn=print,
) -> bool:
    """
    Poll YouTube until the video is fully processed and public, then delete the
    local output file to free disk space.

    Polls every `poll_interval` seconds for up to `max_wait_seconds` (default 2 h).
    Returns True if the file was deleted, False if timed out or an error occurred.
    """
    video_path = Path(video_path)
    deadline = time.time() + max_wait_seconds
    log_fn(
        f"Waiting for YouTube to finish processing {video_id} "
        f"(polling every {poll_interval}s, timeout {max_wait_seconds // 60} min)..."
    )

    while time.time() < deadline:
        try:
            resp = service.videos().list(
                part='status',
                id=video_id,
            ).execute()
            items = resp.get('items', [])
            if items:
                status = items[0].get('status', {})
                upload_status = status.get('uploadStatus')
                privacy_status = status.get('privacyStatus')
                log_fn(f"  Video status: uploadStatus={upload_status}, privacyStatus={privacy_status}")

                if upload_status == 'processed' and privacy_status == 'public':
                    if video_path.exists():
                        video_path.unlink()
                        log_fn(f"Output file deleted (video is live): {video_path.name}")
                    else:
                        log_fn(f"Output file already gone: {video_path.name}")
                    return True

                if upload_status in ('failed', 'rejected'):
                    log_fn(f"Video upload status is '{upload_status}' — retaining output file.")
                    return False

        except HttpError as e:
            log_fn(f"  Warning: API error while checking video status: {e}")
        except Exception as e:
            log_fn(f"  Warning: unexpected error while checking video status: {e}")

        time.sleep(poll_interval)

    log_fn(
        f"Timed out waiting for video to go public after {max_wait_seconds // 60} min "
        f"— output file NOT deleted: {video_path.name}"
    )
    return False
