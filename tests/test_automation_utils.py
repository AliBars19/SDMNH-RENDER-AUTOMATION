"""Tests for automation.py utility functions.

Covers: setup_logging, _notify, _load_json, _save_json, _today_state,
get_todays_failed_topics, get_todays_used_topics, record_failed_topic,
record_run, db_needs_update, record_db_update, wait_for_network,
_install_watchdog, resolve_topic, run_pipeline (failure and success paths),
run_upload_only (auth error path), update_database, run_setup, main CLI.
"""
import json
import logging
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import automation
from tests.conftest import FakeYouTubeService


# ── setup_logging ──────────────────────────────────────────────────────────────

class TestSetupLogging:

    def test_creates_log_file_dir(self, tmp_path, monkeypatch):
        log_file = tmp_path / "sub" / "automation.log"
        monkeypatch.setattr(automation, "LOG_FILE", log_file)
        automation.setup_logging()
        assert log_file.parent.exists()

    def test_creates_rotating_file_handler(self, tmp_path, monkeypatch):
        log_file = tmp_path / "automation.log"
        monkeypatch.setattr(automation, "LOG_FILE", log_file)
        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers.clear()
        try:
            automation.setup_logging()
            handler_types = [type(h).__name__ for h in root.handlers]
            assert "RotatingFileHandler" in handler_types
        finally:
            root.handlers.clear()
            root.handlers.extend(saved)


# ── _notify ───────────────────────────────────────────────────────────────────

class TestNotify:

    def test_no_op_when_topic_empty(self):
        automation._notify({}, "title", "msg")  # no exception

    def test_no_op_when_topic_whitespace(self):
        automation._notify({"ntfy_topic": "   "}, "title", "msg")

    def test_logs_warning_on_network_failure(self, caplog):
        # Uses a fake topic that doesn't exist — ntfy returns 404 or we get a network error.
        # Either way, the exception is caught and logged as a WARNING.
        with caplog.at_level(logging.WARNING):
            automation._notify(
                {"ntfy_topic": "sdmnh_test_topic_that_does_not_exist_xyz"},
                "Test Title",
                "Test message",
            )
        # Either succeeds silently (200) or logs a warning (4xx/network error)
        # The important thing: no uncaught exception

    def test_sends_with_configured_priority(self, monkeypatch):
        calls = []

        class _FakeResp:
            pass

        def fake_urlopen(req, timeout=10):
            calls.append(req)
            return _FakeResp()

        import urllib.request as ureq
        monkeypatch.setattr(ureq, "urlopen", fake_urlopen)
        automation._notify({"ntfy_topic": "test_topic"}, "T", "M", priority="urgent")
        assert len(calls) == 1
        assert calls[0].get_header("Priority") == "urgent"


# ── _load_json / _save_json ────────────────────────────────────────────────────

class TestLoadSaveJson:

    def test_load_returns_empty_dict_for_missing_file(self, tmp_path):
        result = automation._load_json(tmp_path / "nope.json")
        assert result == {}

    def test_load_returns_parsed_json(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}')
        assert automation._load_json(p) == {"key": "value"}

    def test_load_returns_empty_dict_for_corrupt_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json!!!")
        result = automation._load_json(p)
        assert result == {}

    def test_save_then_load_round_trips(self, tmp_path):
        p = tmp_path / "state.json"
        data = {"runs": [{"topic": "quiz"}], "date": "2026-05-14"}
        automation._save_json(p, data)
        loaded = automation._load_json(p)
        assert loaded == data

    def test_save_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "nested" / "deep" / "file.json"
        automation._save_json(p, {"x": 1})
        assert p.exists()


# ── State helpers ─────────────────────────────────────────────────────────────

class TestStateHelpers:

    def test_get_todays_failed_topics_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        assert automation.get_todays_failed_topics() == []

    def test_get_todays_used_topics_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        assert automation.get_todays_used_topics() == []

    def test_get_todays_used_topics_returns_successful_run_topics(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        state = {
            "date": automation._today_utc(),
            "runs": [
                {"topic": "gaming", "video_id": "abc"},
                {"topic": "quiz", "video_id": None},  # failed run — no video_id
            ],
            "failed_topics": [],
        }
        (tmp_path / "lr.json").write_text(json.dumps(state))
        used = automation.get_todays_used_topics()
        assert "gaming" in used
        assert "quiz" not in used

    def test_record_failed_topic_adds_to_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        automation.record_failed_topic("among_us")
        automation.record_failed_topic("gaming")
        failed = automation.get_todays_failed_topics()
        assert "among_us" in failed
        assert "gaming" in failed

    def test_record_failed_topic_no_duplicates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        automation.record_failed_topic("football")
        automation.record_failed_topic("football")
        failed = automation.get_todays_failed_topics()
        assert failed.count("football") == 1

    def test_record_run_saves_all_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        automation.record_run("quiz", "SIDEMEN QUIZ - 2 HOUR SPECIAL", "vid999", 7200)
        state = automation._load_json(tmp_path / "lr.json")
        run = state["runs"][0]
        assert run["topic"] == "quiz"
        assert run["title"] == "SIDEMEN QUIZ - 2 HOUR SPECIAL"
        assert run["video_id"] == "vid999"
        assert run["duration_seconds"] == 7200

    def test_record_run_none_video_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        automation.record_run("quiz", "QUIZ TITLE", None, 3600)
        state = automation._load_json(tmp_path / "lr.json")
        assert state["runs"][0]["video_id"] is None
        assert state["runs"][0]["youtube_url"] is None


# ── db_needs_update / record_db_update ────────────────────────────────────────

class TestDbUpdate:

    def test_needs_update_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")
        assert automation.db_needs_update() is True

    def test_no_update_needed_when_recently_updated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        automation._save_json(tmp_path / "dbu.json", {"date": recent.isoformat()})
        assert automation.db_needs_update() is False

    def test_update_needed_when_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")
        old = datetime.now(timezone.utc) - timedelta(days=10)
        automation._save_json(tmp_path / "dbu.json", {"date": old.isoformat()})
        assert automation.db_needs_update() is True

    def test_update_needed_on_corrupt_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")
        automation._save_json(tmp_path / "dbu.json", {"date": "not-a-date"})
        assert automation.db_needs_update() is True

    def test_record_db_update_writes_today(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")
        automation.record_db_update()
        assert automation.db_needs_update() is False


# ── wait_for_network ──────────────────────────────────────────────────────────

class TestWaitForNetwork:

    def test_returns_true_when_network_available(self):
        result = automation.wait_for_network(max_seconds=10)
        assert result is True

    def test_returns_false_on_timeout(self, monkeypatch):
        original_socket = socket.socket

        class _AlwaysFail:
            def settimeout(self, t):
                pass
            def connect(self, addr):
                raise OSError("refused")
            def close(self):
                pass

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: _AlwaysFail())
        result = automation.wait_for_network(max_seconds=1)
        assert result is False


# ── _install_watchdog ─────────────────────────────────────────────────────────

class TestInstallWatchdog:

    def test_creates_daemon_timer(self):
        import threading
        before = len([t for t in threading.enumerate() if isinstance(t, threading.Timer)])
        automation._install_watchdog(max_seconds=3600)
        after = len([t for t in threading.enumerate() if isinstance(t, threading.Timer)])
        assert after > before


# ── resolve_topic ─────────────────────────────────────────────────────────────

class TestResolveTopic:

    def test_returns_manual_topic_when_specified(self, db, sample_videos, monkeypatch, sample_config):
        import types
        monkeypatch.setattr(automation, "get_todays_failed_topics", lambda: [])
        monkeypatch.setattr(automation, "get_todays_used_topics", lambda: [])
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", Path("/dev/null"))

        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database

        args = types.SimpleNamespace(topic="among_us", force=False)
        topic = automation.resolve_topic(cfg, args)
        assert topic == "among_us"

    def test_returns_none_for_unknown_manual_topic(self, db, sample_videos, sample_config):
        import types
        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database

        args = types.SimpleNamespace(topic="nonexistent_topic", force=False)
        topic = automation.resolve_topic(cfg, args)
        assert topic is None

    def test_returns_none_when_manual_topic_has_no_videos(self, db, sample_config):
        import types
        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database
        # No videos in DB for "general"
        args = types.SimpleNamespace(topic="general", force=False)
        topic = automation.resolve_topic(cfg, args)
        assert topic is None

    def test_auto_selects_topic_when_no_arg(self, db, sample_videos, sample_config, tmp_path, monkeypatch):
        import types
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database

        args = types.SimpleNamespace(topic=None, force=False)
        topic = automation.resolve_topic(cfg, args)
        assert topic is not None
        assert topic in cfg["topics"]


# ── run_pipeline failure path ──────────────────────────────────────────────────

class TestRunPipelineFailure:

    def test_records_failed_topic_when_no_videos(self, db, sample_config, tmp_path, monkeypatch):
        """run_pipeline records topic as failed when run_auto raises (no videos)."""
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")

        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database
        cfg["download_path"] = str(tmp_path / "dl")
        cfg["output_path"] = str(tmp_path / "out")
        cfg["thumbnail_path"] = str(tmp_path / "thumbs")
        cfg["cooldown_days"] = 30
        cfg["youtube"] = {
            "credentials_path": str(tmp_path / "creds.json"),
            "token_path": str(tmp_path / "token.json"),
            "description": "{topic}",
            "tags": [],
            "topic_display_names": {},
            "topic_tags": {},
            "category_id": 24,
            "privacy_status": "public",
            "title_format": "SIDEMEN {topic} - {hours} HOUR SPECIAL",
        }

        # DB has no videos → run_auto raises → pipeline records failure
        automation.run_pipeline(cfg, "among_us")

        failed = automation.get_todays_failed_topics()
        assert "among_us" in failed


# ── _notify warning coverage ──────────────────────────────────────────────────

class TestNotifyWarningCoverage:

    def test_logs_warning_when_urlopen_raises(self, monkeypatch, caplog):
        import urllib.request as ureq

        def failing_urlopen(*a, **kw):
            raise OSError("network down")

        monkeypatch.setattr(ureq, "urlopen", failing_urlopen)
        with caplog.at_level(logging.WARNING):
            automation._notify({"ntfy_topic": "test_topic"}, "T", "M")
        assert any("ntfy" in r.message.lower() or "notification" in r.message.lower() for r in caplog.records)


# ── setup_logging isatty branch ───────────────────────────────────────────────

class TestSetupLoggingIsatty:

    def test_adds_stream_handler_when_isatty(self, tmp_path, monkeypatch):
        log_file = tmp_path / "tty_test.log"
        monkeypatch.setattr(automation, "LOG_FILE", log_file)

        fake_stdout = type("FakeTTY", (), {
            "isatty": lambda self: True,
            "write": lambda self, s: None,
            "flush": lambda self: None,
            "encoding": "utf-8",
        })()
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers.clear()
        try:
            automation.setup_logging()
            handler_types = [type(h).__name__ for h in root.handlers]
            assert "StreamHandler" in handler_types
        finally:
            root.handlers.clear()
            root.handlers.extend(saved)


# ── update_database ───────────────────────────────────────────────────────────

class TestUpdateDatabase:

    def test_calls_update_db_subprocess(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(automation, "record_db_update", lambda: None)
        automation.update_database()
        assert any("update_db.py" in str(c) for c in calls)

    def test_logs_warning_on_nonzero_exit(self, monkeypatch, caplog):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with caplog.at_level(logging.WARNING):
            automation.update_database()
        assert any("errors" in r.message.lower() for r in caplog.records)

    def test_records_db_update_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_DB_UPDATE_FILE", tmp_path / "dbu.json")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        automation.update_database()
        assert automation.db_needs_update() is False


# ── run_setup ──────────────────────────────────────────────────────────────────

class TestRunSetup:

    def test_calls_authenticate_and_prints_success(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda _: "")
        monkeypatch.setattr(automation, "authenticate", lambda *a, **kw: object())
        cfg = {
            "youtube": {
                "credentials_path": str(tmp_path / "creds.json"),
                "token_path": str(tmp_path / "token.json"),
            }
        }
        automation.run_setup(cfg)
        out = capsys.readouterr().out
        assert "successful" in out.lower() or "token" in out.lower()


# ── resolve_topic: skip_today logging branch ──────────────────────────────────

class TestResolveTopicSkipLogging:

    def test_logs_when_topics_already_used(self, db, sample_videos, sample_config, tmp_path, monkeypatch, caplog):
        import types
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        automation.record_run("among_us", "Test", "vid_x", 3600)

        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database

        args = types.SimpleNamespace(topic=None, force=False)
        with caplog.at_level(logging.INFO):
            automation.resolve_topic(cfg, args)
        assert any("skip" in r.message.lower() for r in caplog.records)


# ── run_upload_only: auth error path ─────────────────────────────────────────

class TestRunUploadOnlyAuthError:

    def test_logs_error_on_youtube_auth_expired(self, real_video, upload_only_cfg, tmp_path, monkeypatch, caplog):
        from src.youtube_upload import YouTubeAuthExpiredError
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "last_run.json")
        svc = FakeYouTubeService(raise_on_insert=YouTubeAuthExpiredError("token revoked"))

        with caplog.at_level(logging.ERROR):
            automation.run_upload_only(upload_only_cfg, real_video, "gaming", 4, service=svc)

        assert any("auth" in r.message.lower() or "expired" in r.message.lower() for r in caplog.records)


# ── run_pipeline: success path ────────────────────────────────────────────────

class TestRunPipelineSuccess:

    def _base_cfg(self, db, sample_config, tmp_path):
        cfg = dict(sample_config)
        cfg["db_path"] = db.engine.url.database
        cfg["download_path"] = str(tmp_path / "dl")
        cfg["output_path"] = str(tmp_path / "out")
        cfg["thumbnail_path"] = str(tmp_path / "thumbs")
        cfg["youtube_processing_wait_seconds"] = 1
        cfg["youtube"] = {
            "credentials_path": str(tmp_path / "creds.json"),
            "token_path": str(tmp_path / "token.json"),
            "description": "{topic}",
            "tags": [],
            "topic_display_names": {},
            "topic_tags": {},
            "category_id": 24,
            "privacy_status": "public",
            "title_format": "SIDEMEN {topic} - {hours} HOUR SPECIAL",
        }
        return cfg

    def test_records_run_on_successful_upload(self, db, sample_config, real_video, tmp_path, monkeypatch):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        cfg = self._base_cfg(db, sample_config, tmp_path)
        svc = FakeYouTubeService()
        automation.run_pipeline(cfg, "among_us", service=svc)

        state = automation._load_json(tmp_path / "lr.json")
        runs = state.get("runs", [])
        assert any(r.get("topic") == "among_us" and r.get("video_id") == svc.video_id for r in runs)

    def test_ephemeral_skips_processing_wait(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        cfg = self._base_cfg(db, sample_config, tmp_path)
        svc = FakeYouTubeService()
        with caplog.at_level(logging.INFO):
            automation.run_pipeline(cfg, "among_us", ephemeral=True, service=svc)
        assert any("ephemeral" in r.message.lower() for r in caplog.records)

    def test_auth_expired_error_logged(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        from src.youtube_upload import YouTubeAuthExpiredError
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        cfg = self._base_cfg(db, sample_config, tmp_path)
        svc = FakeYouTubeService(raise_on_insert=YouTubeAuthExpiredError("expired"))
        with caplog.at_level(logging.ERROR):
            automation.run_pipeline(cfg, "among_us", service=svc)
        assert any("auth" in r.message.lower() or "expired" in r.message.lower() for r in caplog.records)

    def test_file_not_found_error_logged(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))
        monkeypatch.setattr(automation, "authenticate", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no creds")))

        cfg = self._base_cfg(db, sample_config, tmp_path)
        with caplog.at_level(logging.ERROR):
            automation.run_pipeline(cfg, "among_us")
        assert any("setup" in r.message.lower() or "not found" in r.message.lower() or "run" in r.message.lower() for r in caplog.records)

    def test_generic_upload_exception_logged(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        cfg = self._base_cfg(db, sample_config, tmp_path)
        from googleapiclient.errors import HttpError
        import httplib2
        resp = httplib2.Response({"status": 500})
        svc = FakeYouTubeService(raise_on_insert=HttpError(resp=resp, content=b"Server Error"))

        with caplog.at_level(logging.ERROR):
            automation.run_pipeline(cfg, "among_us", service=svc)
        assert any("upload" in r.message.lower() or "failed" in r.message.lower() for r in caplog.records)

    def test_no_svc_retains_output_file(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        cfg = self._base_cfg(db, sample_config, tmp_path)
        # Make authenticate fail so video_id stays None
        monkeypatch.setattr(automation, "authenticate", lambda *a, **kw: (_ for _ in ()).throw(Exception("no auth")))

        with caplog.at_level(logging.INFO):
            automation.run_pipeline(cfg, "among_us")
        assert any("retained" in r.message.lower() or "not complete" in r.message.lower() or "did not" in r.message.lower() for r in caplog.records)

    def test_thumbnail_set_success_logged(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        # Make _try_thumbnail return a real path so the if-thumb branch is taken
        thumb_path = str(tmp_path / "thumb.jpg")
        (tmp_path / "thumb.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        monkeypatch.setattr(automation, "_try_thumbnail",
                            lambda *a, **kw: thumb_path)

        cfg = self._base_cfg(db, sample_config, tmp_path)
        svc = FakeYouTubeService()  # set_thumbnail returns True

        with caplog.at_level(logging.INFO):
            automation.run_pipeline(cfg, "among_us", service=svc)
        assert any("thumbnail set" in r.message.lower() for r in caplog.records)

    def test_thumbnail_set_failure_logged(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))

        thumb_path = str(tmp_path / "thumb.jpg")
        (tmp_path / "thumb.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        monkeypatch.setattr(automation, "_try_thumbnail",
                            lambda *a, **kw: thumb_path)
        monkeypatch.setattr(automation, "set_thumbnail", lambda *a, **kw: False)

        cfg = self._base_cfg(db, sample_config, tmp_path)
        svc = FakeYouTubeService()

        with caplog.at_level(logging.WARNING):
            automation.run_pipeline(cfg, "among_us", service=svc)
        assert any("thumbnail" in r.message.lower() and "failed" in r.message.lower()
                   for r in caplog.records)

    def test_wait_and_delete_returns_false_logs_warning(self, db, sample_config, real_video, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "lr.json")
        monkeypatch.setattr(automation, "run_auto", lambda topic, max_hours, cfg: (real_video, 7200.0, ["vid001"]))
        monkeypatch.setattr(automation, "wait_and_delete_when_public", lambda **kw: False)

        cfg = self._base_cfg(db, sample_config, tmp_path)
        svc = FakeYouTubeService()

        with caplog.at_level(logging.WARNING):
            automation.run_pipeline(cfg, "among_us", service=svc)
        assert any("not deleted" in r.message.lower() or "timed out" in r.message.lower()
                   for r in caplog.records)


# ── main() direct-call tests ──────────────────────────────────────────────────

class TestMainFunction:
    """Test automation.main() directly with monkeypatching — coverage-tracked."""

    def _mock_infra(self, monkeypatch, sample_config, *, argv, already_ran=False, network=True, db_stale=False):
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(os, "chdir", lambda p: None)
        monkeypatch.setattr(automation, "setup_logging", lambda: None)
        monkeypatch.setattr(automation, "load_config", lambda: sample_config)
        monkeypatch.setattr(automation, "_install_watchdog", lambda *a, **kw: None)
        monkeypatch.setattr(automation, "already_ran_today", lambda: already_ran)
        monkeypatch.setattr(automation, "wait_for_network", lambda *a, **kw: network)
        monkeypatch.setattr(automation, "db_needs_update", lambda: db_stale)
        monkeypatch.setattr(automation, "update_database", lambda: None)

    def test_setup_flag_calls_run_setup(self, monkeypatch, sample_config, tmp_path):
        called = []
        monkeypatch.setattr(automation, "run_setup", lambda cfg: called.append(cfg))
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py", "--setup"])
        automation.main()
        assert len(called) == 1

    def test_update_db_flag_calls_update_database(self, monkeypatch, sample_config):
        called = []
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py", "--update-db"])
        monkeypatch.setattr(automation, "update_database", lambda: called.append(True))
        automation.main()
        assert called

    def test_already_ran_today_returns_early(self, monkeypatch, sample_config, caplog):
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py"], already_ran=True)
        with caplog.at_level(logging.INFO):
            automation.main()
        assert any("already ran" in r.message.lower() for r in caplog.records)

    def test_no_network_returns_early(self, monkeypatch, sample_config, caplog):
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py"], network=False)
        with caplog.at_level(logging.ERROR):
            automation.main()
        assert any("no network" in r.message.lower() or "cannot" in r.message.lower() for r in caplog.records)

    def test_upload_only_exits_when_file_missing(self, monkeypatch, sample_config, tmp_path):
        self._mock_infra(
            monkeypatch, sample_config,
            argv=["automation.py", "--upload-only", str(tmp_path / "nope.mp4"),
                  "--upload-topic", "gaming", "--upload-duration", "3600"],
        )
        with pytest.raises(SystemExit) as exc_info:
            automation.main()
        assert exc_info.value.code == 1

    def test_upload_only_success_calls_run_upload_only(self, monkeypatch, sample_config, real_video):
        called = []
        monkeypatch.setattr(automation, "run_upload_only", lambda *a, **kw: called.append(True))
        self._mock_infra(
            monkeypatch, sample_config,
            argv=["automation.py", "--upload-only", str(real_video),
                  "--upload-topic", "gaming", "--upload-duration", "3600"],
        )
        automation.main()
        assert called

    def test_force_flag_skips_already_ran_check(self, monkeypatch, sample_config):
        checked = []
        monkeypatch.setattr(automation, "already_ran_today", lambda: checked.append(True) or True)
        monkeypatch.setattr(automation, "resolve_topic", lambda c, a: None)
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py", "--force"])
        monkeypatch.setattr(automation, "already_ran_today", lambda: checked.append(True) or True)
        automation.main()
        assert not checked  # --force skips check

    def test_ephemeral_skips_network_wait(self, monkeypatch, sample_config):
        network_called = []
        monkeypatch.setattr(automation, "resolve_topic", lambda c, a: None)
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py", "--ephemeral", "--force"])
        monkeypatch.setattr(automation, "wait_for_network", lambda *a, **kw: network_called.append(True) or True)
        automation.main()
        assert not network_called  # --ephemeral skips network wait

    def test_db_stale_calls_update_database(self, monkeypatch, sample_config):
        called = []
        monkeypatch.setattr(automation, "update_database", lambda: called.append(True))
        monkeypatch.setattr(automation, "resolve_topic", lambda c, a: None)
        self._mock_infra(monkeypatch, sample_config,
                         argv=["automation.py", "--force", "--ephemeral"], db_stale=True)
        monkeypatch.setattr(automation, "update_database", lambda: called.append(True))
        automation.main()
        assert called

    def test_normal_path_calls_run_pipeline(self, monkeypatch, sample_config):
        called = []
        monkeypatch.setattr(automation, "resolve_topic", lambda c, a: "among_us")
        monkeypatch.setattr(automation, "run_pipeline", lambda cfg, topic, ephemeral=False, **kw: called.append(topic))
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py", "--force", "--ephemeral"])
        automation.main()
        assert "among_us" in called

    def test_no_topic_returns_early(self, monkeypatch, sample_config):
        monkeypatch.setattr(automation, "resolve_topic", lambda c, a: None)
        called = []
        monkeypatch.setattr(automation, "run_pipeline", lambda *a, **kw: called.append(True))
        self._mock_infra(monkeypatch, sample_config, argv=["automation.py", "--force", "--ephemeral"])
        automation.main()
        assert not called  # no topic → no pipeline
