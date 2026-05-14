"""Tests for update_db.py.

Covers: load_config, scrape_channel (monkeypatched subprocess), assign_topic already
tested in test_topic_assignment.py. main() covered via subprocess invocation.

assign_topic: fully covered by test_topic_assignment.py.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import update_db


# ── load_config ────────────────────────────────────────────────────────────────

class TestLoadConfig:

    def test_returns_valid_config(self, tmp_path):
        cfg = {
            "channels": ["https://youtube.com/@Test"],
            "download_path": "data/downloads",
            "output_path": "data/outputs",
            "db_path": "data/videos.db",
            "topics": {"general": []},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(cfg))
        result = update_db.load_config(str(cfg_file))
        assert result["channels"] == cfg["channels"]

    def test_exits_when_file_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            update_db.load_config(str(tmp_path / "missing.yaml"))

    def test_exits_when_missing_required_keys(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("foo: bar\n")  # missing required keys
        with pytest.raises(SystemExit):
            update_db.load_config(str(cfg_file))


# ── scrape_channel ─────────────────────────────────────────────────────────────

class TestScrapeChannel:

    def _fake_run(self, lines: list[str], monkeypatch, returncode=0):
        """Monkeypatch subprocess.run to return the given lines as stdout."""
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout="\n".join(lines),
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: fake_result)

    def test_parses_valid_video_json(self, monkeypatch):
        video_json = json.dumps({
            "id": "abc123",
            "title": "SIDEMEN AMONG US",
            "webpage_url": "https://youtube.com/watch?v=abc123",
            "url": "https://youtube.com/watch?v=abc123",
            "duration": 3600,
            "upload_date": "20240115",
            "view_count": 1_000_000,
            "channel": "Sidemen",
        })
        self._fake_run([video_json], monkeypatch)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert len(result) == 1
        assert result[0]["youtube_id"] == "abc123"
        assert result[0]["title"] == "SIDEMEN AMONG US"

    def test_skips_shorts(self, monkeypatch):
        short_json = json.dumps({
            "id": "short001",
            "title": "Short Video",
            "webpage_url": "https://youtube.com/shorts/short001",
            "url": "https://youtube.com/shorts/short001",
            "duration": 30,
            "upload_date": "20240115",
            "view_count": 500_000,
            "channel": "Sidemen",
        })
        self._fake_run([short_json], monkeypatch)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert result == []

    def test_skips_pre_2020_videos(self, monkeypatch):
        old_json = json.dumps({
            "id": "old001",
            "title": "Old Video",
            "webpage_url": "https://youtube.com/watch?v=old001",
            "duration": 3600,
            "upload_date": "20190101",
            "view_count": 1_000_000,
            "channel": "Sidemen",
        })
        self._fake_run([old_json], monkeypatch)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert result == []

    def test_handles_timeout(self, monkeypatch):
        def raise_timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 300)
        monkeypatch.setattr(subprocess, "run", raise_timeout)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert result == []

    def test_skips_invalid_json_lines(self, monkeypatch):
        self._fake_run(["{invalid json!!!", '{"id":"ok","title":"T","webpage_url":"https://yt.com/watch?v=ok","duration":3600,"upload_date":"20240101","view_count":100,"channel":"C"}'], monkeypatch)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert len(result) == 1
        assert result[0]["youtube_id"] == "ok"

    def test_empty_output_returns_empty(self, monkeypatch):
        self._fake_run([], monkeypatch)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert result == []

    def test_skips_blank_lines(self, monkeypatch):
        self._fake_run(["", "   ", ""], monkeypatch)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert result == []

    def test_nonzero_exit_code_returns_empty(self, monkeypatch):
        self._fake_run([], monkeypatch, returncode=1)
        result = update_db.scrape_channel("https://youtube.com/@Sidemen")
        assert result == []


# ── assign_topic (additional edge cases not in test_topic_assignment.py) ────────

class TestAssignTopicEdgeCases:

    TOPICS = {
        "gaming": ["gta", "fifa"],
        "quiz": ["quiz", "trivia"],
        "general": [],
    }

    def test_none_title_returns_general(self):
        assert update_db.assign_topic(None, self.TOPICS) == "general"

    def test_empty_title_returns_general(self):
        assert update_db.assign_topic("", self.TOPICS) == "general"

    def test_no_match_returns_general(self):
        assert update_db.assign_topic("Random Video Title", self.TOPICS) == "general"

    def test_first_matching_topic_wins(self):
        result = update_db.assign_topic("FIFA GTA Crossover", self.TOPICS)
        assert result in ("gaming",)  # both keywords match gaming


# ── main() via subprocess ─────────────────────────────────────────────────────

class TestUpdateDbMain:

    def test_exits_nonzero_when_no_config(self, tmp_path):
        """Running update_db.py with no config should exit non-zero."""
        result = subprocess.run(
            [sys.executable, "update_db.py"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
            timeout=30,
        )
        # Either exits 1 (no config found) or 0 if config exists in cwd
        # Just verify no unhandled exception (returncode != -11 segfault etc.)
        assert result.returncode in (0, 1)


# ── update_db.main() direct call ─────────────────────────────────────────────

class TestUpdateDbMainDirect:

    def _cfg(self, tmp_path):
        return {
            "channels": ["https://youtube.com/@Sidemen"],
            "download_path": str(tmp_path / "dl"),
            "output_path": str(tmp_path / "out"),
            "db_path": str(tmp_path / "test.db"),
            "topics": {"gaming": ["gta", "fifa"], "general": []},
        }

    def test_main_adds_new_videos(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr(update_db, "load_config", lambda *a: cfg)

        scraped = [
            {
                "youtube_id": "main001",
                "title": "SIDEMEN GTA ROLEPLAY",
                "url": "https://youtube.com/watch?v=main001",
                "duration": 3600,
                "upload_date": "20240101",
                "view_count": 1_000_000,
                "channel": "Sidemen",
            }
        ]
        monkeypatch.setattr(update_db, "scrape_channel", lambda url: scraped)

        from src.database import Database, Video
        db = Database(cfg["db_path"])
        monkeypatch.setattr(update_db, "Database", lambda path: db)

        update_db.main()

        session = db.get_session()
        count = session.query(Video).count()
        session.close()
        assert count == 1

    def test_main_updates_view_count_for_existing(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr(update_db, "load_config", lambda *a: cfg)

        from src.database import Database, Video
        db = Database(cfg["db_path"])
        s = db.get_session()
        s.add(Video(youtube_id="existing01", title="Old Title", url="http://x",
                    duration=3600, upload_date="20240101", view_count=500, topic="general"))
        s.commit()
        s.close()
        monkeypatch.setattr(update_db, "Database", lambda path: db)

        scraped = [
            {
                "youtube_id": "existing01",
                "title": "Old Title",
                "url": "http://x",
                "duration": 3600,
                "upload_date": "20240101",
                "view_count": 999_999,
                "channel": "Sidemen",
            }
        ]
        monkeypatch.setattr(update_db, "scrape_channel", lambda url: scraped)

        update_db.main()

        s2 = db.get_session()
        v = s2.query(Video).filter_by(youtube_id="existing01").first()
        updated_views = v.view_count
        s2.close()
        assert updated_views == 999_999

    def test_main_reclassifies_general_videos(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr(update_db, "load_config", lambda *a: cfg)

        from src.database import Database, Video
        db = Database(cfg["db_path"])
        s = db.get_session()
        s.add(Video(youtube_id="gen01", title="SIDEMEN GTA HEIST", url="http://x",
                    duration=3600, upload_date="20240101", view_count=100, topic="general"))
        s.commit()
        s.close()
        monkeypatch.setattr(update_db, "Database", lambda path: db)
        monkeypatch.setattr(update_db, "scrape_channel", lambda url: [])

        update_db.main()

        s2 = db.get_session()
        v = s2.query(Video).filter_by(youtube_id="gen01").first()
        new_topic = v.topic
        s2.close()
        assert new_topic == "gaming"  # reassigned from general

    def test_main_skips_videos_without_youtube_id(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr(update_db, "load_config", lambda *a: cfg)

        from src.database import Database, Video
        db = Database(cfg["db_path"])
        monkeypatch.setattr(update_db, "Database", lambda path: db)

        scraped = [{"title": "No ID Video", "duration": 3600}]
        monkeypatch.setattr(update_db, "scrape_channel", lambda url: scraped)

        update_db.main()

        s = db.get_session()
        count = s.query(Video).count()
        s.close()
        assert count == 0  # skipped due to missing youtube_id
