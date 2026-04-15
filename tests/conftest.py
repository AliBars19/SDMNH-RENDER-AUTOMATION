import pytest
from src.database import Database, Video, Compilation


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite database for testing."""
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
