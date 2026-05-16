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

import logging
import os
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


class YouTubeAuthExpiredError(Exception):
    """Raised when the OAuth token is missing or has been revoked."""

import time

# Scopes required: upload videos + manage thumbnails
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube',
]

# Default title format used if youtube.title_format is missing from config.
DEFAULT_TITLE_FORMAT = "SIDEMEN {topic} - {hours} HOUR SPECIAL"


# ── Authentication ─────────────────────────────────────────────────────────────

def authenticate(credentials_path: str, token_path: str, setup_mode: bool = False):
    """
    Authenticate with the YouTube Data API using OAuth 2.0.

    In setup_mode=True a browser window opens for user consent (or binds to
    0.0.0.0:8081 when SDMNH_HEADLESS is set, for SSH tunnel auth).
    Subsequent calls reuse the cached token from token_path.

    Raises YouTubeAuthExpiredError if the token is invalid and setup_mode is False.
    Returns a googleapiclient Resource object ready to make API calls.
    """
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except google.auth.exceptions.RefreshError as exc:
                # Token revoked (invalid_grant) — delete stale file, surface clearly
                Path(token_path).unlink(missing_ok=True)
                raise YouTubeAuthExpiredError(
                    "YouTube OAuth token revoked (invalid_grant). "
                    "Re-auth via SSH tunnel:\n"
                    "  ssh -L 8081:localhost:8081 root@138.68.186.172\n"
                    "  SDMNH_HEADLESS=1 python automation.py --setup"
                ) from exc
        elif not setup_mode:
            raise YouTubeAuthExpiredError(
                "No valid YouTube token found. "
                "Run:  SDMNH_HEADLESS=1 python automation.py --setup"
            )
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"YouTube credentials file not found: {credentials_path}\n"
                    "Please follow the setup instructions at the top of src/youtube_upload.py "
                    "and run:  python automation.py --setup"
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            if os.environ.get("SDMNH_HEADLESS"):
                # Headless server: bind to all interfaces so an SSH tunnel can reach it
                # ssh -L 8081:localhost:8081 root@<server>
                creds = flow.run_local_server(bind_addr="0.0.0.0", port=8081, open_browser=False)
            else:
                creds = flow.run_local_server(port=0)

        # Persist token so next run doesn't need a browser
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    return build('youtube', 'v3', credentials=creds)


# ── Title & metadata helpers ───────────────────────────────────────────────────

def format_title(
    topic: str,
    duration_seconds: float,
    display_names: dict[str, str],
    title_format: str = DEFAULT_TITLE_FORMAT,
) -> str:
    """
    Return the YouTube video title from a config-driven template.

    title_format supports {topic} and {hours} placeholders. Hours are rounded
    to the nearest 0.5 (minimum 1). "3.5" is displayed as "3.5", "3.0" as "3".
    Unknown topics fall back to upper-cased topic key.
    """
    topic_display = display_names.get(topic, topic.replace('_', ' ').upper())
    hours_raw = max(1.0, round(duration_seconds / 3600 * 2) / 2)
    hours = f"{hours_raw:g}"
    return title_format.format(topic=topic_display, hours=hours)


CHANNEL_SUFFIX_RE = re.compile(
    r"\s*\((?:sidemen gaming|moresidemen|more sidemen|sidemen reacts|sidemen shorts)\)\s*$",
    re.IGNORECASE,
)

YT_TITLE_MAX_LEN = 100


def _clean_video_title(raw: str) -> str:
    """Strip channel-suffix parentheticals and trim whitespace."""
    if not raw:
        return ""
    cleaned = CHANNEL_SUFFIX_RE.sub("", raw).strip()
    # Collapse trailing punctuation that looks dangling after suffix strip
    return cleaned.rstrip(" -|·•")


def format_title_from_lead(
    selected_videos: list,
    duration_seconds: float,
    *,
    fallback_topic: str = "",
    display_names: dict[str, str] | None = None,
    title_format: str = DEFAULT_TITLE_FORMAT,
) -> str:
    """
    Build a YouTube title from the lead (first) selected video's title.

    Strips channel-suffix parentheticals and appends a duration marker. Falls
    back to the template-based format_title if no usable lead title exists.
    Truncates to 100 chars (YouTube limit) — duration suffix is preserved by
    trimming the base title, not the suffix.
    """
    hours_raw = max(1.0, round(duration_seconds / 3600 * 2) / 2)
    hours = f"{hours_raw:g}"
    suffix = f" | {hours} HOUR COMPILATION"

    lead = selected_videos[0] if selected_videos else None
    raw_title = getattr(lead, "title", None) if lead is not None else None
    if not isinstance(raw_title, str):
        raw_title = ""
    base = _clean_video_title(raw_title)

    if not base:
        return format_title(fallback_topic, duration_seconds, display_names or {}, title_format)

    max_base = YT_TITLE_MAX_LEN - len(suffix)
    if len(base) > max_base:
        base = base[: max_base - 1].rstrip() + "…"

    return f"{base}{suffix}"


def format_description(
    topic: str,
    description_template: str,
    display_names: dict[str, str],
) -> str:
    """Fill in the description template with topic-specific values."""
    topic_display = display_names.get(topic, topic.replace('_', ' ').title())
    topic_tag = topic.replace('_', '').replace(' ', '')
    return description_template.format(topic=topic_display, topic_tag=topic_tag)


def build_tags(
    topic: str,
    base_tags: list[str],
    topic_tags: dict[str, list[str]],
) -> list[str]:
    """Combine base tags with per-topic tags (deduplicated). Max 500."""
    topic_word = topic.replace('_', ' ')
    extra = topic_tags.get(topic, [topic_word])

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

    logger.info("Uploading: %s", title)
    logger.info("File: %s", Path(video_path).name)
    size_gb = Path(video_path).stat().st_size / (1024 ** 3)
    logger.info("Size: %.2f GB", size_gb)

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = insert_request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info("Upload progress: %d%%", pct)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                retry_count += 1
                if retry_count > 10:
                    raise Exception(f"Upload failed after {retry_count} retries: {e}") from e
                wait = min(2 ** retry_count, 64)
                logger.warning("Server error %d — retrying in %ds...", e.resp.status, wait)
                time.sleep(wait)
            else:
                raise

    logger.info("Upload complete.")
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
        logger.warning("Thumbnail upload failed: %s", e)
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
