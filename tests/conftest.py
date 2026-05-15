import subprocess
from pathlib import Path

import pytest

from src.database import Database, Video, Compilation


# ── Database fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Real SQLite database for testing."""
    db_path = str(tmp_path / "test.db")
    return Database(db_path)


@pytest.fixture
def session(db):
    """Database session that auto-closes after test."""
    s = db.get_session()
    yield s
    s.close()


@pytest.fixture
def sample_videos(session):
    """Insert sample videos across multiple topics."""
    videos = [
        Video(youtube_id="vid001", title="SIDEMEN AMONG US", url="https://youtube.com/watch?v=vid001",
              duration=3600, upload_date="20240115", view_count=15000000, channel="Sidemen", topic="among_us"),
        Video(youtube_id="vid002", title="SIDEMEN TRY NOT TO LAUGH", url="https://youtube.com/watch?v=vid002",
              duration=5400, upload_date="20240210", view_count=8000000, channel="Sidemen", topic="try_not_to_laugh"),
        Video(youtube_id="vid003", title="SIDEMEN MUKBANG", url="https://youtube.com/watch?v=vid003",
              duration=7200, upload_date="20240305", view_count=5000000, channel="MoreSidemen", topic="mukbang"),
        Video(youtube_id="vid004", title="SIDEMEN RANDOM VIDEO", url="https://youtube.com/watch?v=vid004",
              duration=1800, upload_date="20240420", view_count=2000000, channel="Sidemen", topic="general"),
        Video(youtube_id="vid005", title="SIDEMEN AMONG US 2", url="https://youtube.com/watch?v=vid005",
              duration=4200, upload_date="20240501", view_count=20000000, channel="Sidemen", topic="among_us"),
    ]
    session.add_all(videos)
    session.commit()
    return videos


@pytest.fixture
def sample_config():
    """Minimal config dict for testing."""
    return {
        "channels": ["https://www.youtube.com/@Sidemen"],
        "download_path": "data/downloads",
        "output_path": "data/outputs",
        "db_path": "data/videos.db",
        "cooldown_days": 30,
        "max_compilation_hours": 12,
        "topics": {
            "among_us": ["among us"],
            "try_not_to_laugh": ["try not to laugh", "tntl"],
            "mukbang": ["mukbang", "eating challenge"],
            "general": [],
        },
    }


# ── Real ffmpeg video fixtures ────────────────────────────────────────────────

def _make_video(path: Path, duration: int = 5, audio_codec: str = "aac") -> Path:
    """Create a minimal real mp4 with real video+audio streams via ffmpeg."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", audio_codec,
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def real_video(tmp_path) -> Path:
    """A real 5-second AAC mp4 usable as input for ffprobe/ffmpeg tests."""
    return _make_video(tmp_path / "video_aac.mp4", duration=5, audio_codec="aac")


@pytest.fixture
def two_aac_videos(tmp_path) -> dict:
    """Two real AAC mp4s — same audio codec, should all pass the filter."""
    a = _make_video(tmp_path / "a.mp4", audio_codec="aac")
    b = _make_video(tmp_path / "b.mp4", audio_codec="aac")
    return {"id_a": a, "id_b": b}


@pytest.fixture
def mixed_codec_videos(tmp_path) -> dict:
    """Two AAC videos + one MP3 video — MP3 is the minority and should be dropped."""
    a = _make_video(tmp_path / "aac1.mp4", audio_codec="aac")
    b = _make_video(tmp_path / "aac2.mp4", audio_codec="aac")
    c = _make_video(tmp_path / "mp3.mp4", audio_codec="libmp3lame")
    return {"id_aac1": a, "id_aac2": b, "id_mp3": c}


# ── Fake YouTube service (dependency injection) ────────────────────────────────

class _FakeInsertRequest:
    """Simulates a resumable-upload insert request."""

    def __init__(self, video_id: str, raise_exc=None):
        self._video_id = video_id
        self._raise_exc = raise_exc
        self._called = False

    def next_chunk(self):
        if self._raise_exc:
            raise self._raise_exc
        if not self._called:
            self._called = True
            return None, {"id": self._video_id}
        return None, {"id": self._video_id}


class _FakeListRequest:
    def execute(self):
        return {
            "items": [{
                "status": {
                    "uploadStatus": "processed",
                    "privacyStatus": "public",
                }
            }]
        }


class _FakeThumbRequest:
    def execute(self):
        return {}


class _FakeVideosResource:
    def __init__(self, video_id: str, raise_on_insert=None):
        self._video_id = video_id
        self._raise_on_insert = raise_on_insert

    def insert(self, **kwargs):
        return _FakeInsertRequest(self._video_id, raise_exc=self._raise_on_insert)

    def list(self, **kwargs):
        return _FakeListRequest()


class _FakeThumbnailsResource:
    def __init__(self):
        self.set_called = False
        self.last_video_id = None

    def set(self, videoId=None, **kwargs):
        self.set_called = True
        self.last_video_id = videoId
        return _FakeThumbRequest()


class FakeYouTubeService:
    """
    Real test double for the Google API YouTube service object.
    Implements the same interface used by upload_video, set_thumbnail,
    and wait_and_delete_when_public — no network calls, no mocking.
    """

    def __init__(self, video_id: str = "fake_vid_123", raise_on_insert=None):
        self.video_id = video_id
        self._raise_on_insert = raise_on_insert
        self.thumbnails_resource = _FakeThumbnailsResource()

    def videos(self):
        return _FakeVideosResource(self.video_id, raise_on_insert=self._raise_on_insert)

    def thumbnails(self):
        return self.thumbnails_resource


@pytest.fixture
def fake_yt_service() -> FakeYouTubeService:
    """Injected fake YouTube service for upload/thumbnail tests."""
    return FakeYouTubeService(video_id="test_vid_abc")


@pytest.fixture
def upload_only_cfg(tmp_path) -> dict:
    """Minimal config for run_upload_only tests."""
    return {
        "thumbnail_path": str(tmp_path / "thumbnails"),
        "youtube_processing_wait_seconds": 1,
        "youtube": {
            "credentials_path": str(tmp_path / "client_secrets.json"),
            "token_path": str(tmp_path / "token.json"),
            "description": "Best {topic} moments! #{topic_tag}",
            "tags": ["sidemen"],
            "topic_display_names": {"among_us": "AMONG US", "gaming": "GAMING"},
            "topic_tags": {},
            "category_id": 24,
            "privacy_status": "public",
            "title_format": "SIDEMEN {topic} - {hours} HOUR SPECIAL",
        },
    }
