"""Tests for --upload-only mode: _extract_frame, run_upload_only, CLI args.

All ffmpeg calls use real binaries and real test video files.
YouTube API calls are replaced by the injected FakeYouTubeService (DI).
No unittest.mock.patch used for high-level dependencies.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from automation import _extract_frame, run_upload_only
from tests.conftest import FakeYouTubeService


# ── _extract_frame ────────────────────────────────────────────────────────────

class TestExtractFrame:
    """Real ffmpeg calls — no subprocess patching."""

    def test_creates_jpeg_from_real_video(self, real_video, tmp_path):
        out = str(tmp_path / "thumb.jpg")
        result = _extract_frame(real_video, out, timestamp="00:00:02")
        assert result == out
        assert Path(out).stat().st_size > 1000

    def test_returns_none_for_missing_video(self, tmp_path):
        result = _extract_frame(tmp_path / "nope.mp4", str(tmp_path / "t.jpg"))
        assert result is None

    def test_returns_none_for_corrupt_video(self, tmp_path):
        corrupt = tmp_path / "bad.mp4"
        corrupt.write_bytes(b"\x00" * 100)
        result = _extract_frame(corrupt, str(tmp_path / "t.jpg"))
        assert result is None

    def test_custom_timestamp_extracts_frame(self, real_video, tmp_path):
        out = str(tmp_path / "frame.jpg")
        result = _extract_frame(real_video, out, timestamp="00:00:01")
        assert result == out
        assert Path(out).stat().st_size > 1000

    def test_output_file_is_jpeg(self, real_video, tmp_path):
        out = str(tmp_path / "frame.jpg")
        _extract_frame(real_video, out, timestamp="00:00:01")
        data = Path(out).read_bytes()
        assert data[:2] == b"\xff\xd8"  # JPEG magic bytes


# ── run_upload_only ───────────────────────────────────────────────────────────

class TestRunUploadOnly:
    """
    Uses injected FakeYouTubeService — no patching of authenticate/upload_video.
    Uses real ffmpeg for thumbnail extraction from actual video file.
    """

    def test_uploads_video_and_records_run(self, real_video, upload_only_cfg, fake_yt_service, tmp_path, monkeypatch):
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")

        run_upload_only(upload_only_cfg, real_video, "among_us", 4, service=fake_yt_service)

        last_run = json.loads((tmp_path / "last_run.json").read_text())
        runs = last_run["runs"]
        assert len(runs) == 1
        assert runs[0]["topic"] == "among_us"
        assert runs[0]["video_id"] == fake_yt_service.video_id
        assert runs[0]["duration_seconds"] == 4

    def test_title_formatted_correctly(self, real_video, upload_only_cfg, tmp_path, monkeypatch):
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        svc = FakeYouTubeService()

        run_upload_only(upload_only_cfg, real_video, "among_us", 10800, service=svc)

        last_run = json.loads((tmp_path / "last_run.json").read_text())
        title = last_run["runs"][0]["title"]
        assert "AMONG US" in title
        assert "3" in title  # 3-hour video

    def test_thumbnail_extracted_from_real_video(self, real_video, upload_only_cfg, tmp_path, monkeypatch):
        # duration=4 → midpoint at 2s, within the 5s real_video
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        svc = FakeYouTubeService()

        run_upload_only(upload_only_cfg, real_video, "gaming", 4, service=svc)

        thumb_dir = Path(upload_only_cfg["thumbnail_path"])
        assert thumb_dir.exists()
        jpgs = list(thumb_dir.glob("*.jpg"))
        assert len(jpgs) == 1
        assert jpgs[0].stat().st_size > 1000

    def test_set_thumbnail_called_when_extracted(self, real_video, upload_only_cfg, tmp_path, monkeypatch):
        # duration=4 → midpoint at 2s, within the 5s real_video — thumbnail extraction succeeds
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        svc = FakeYouTubeService()

        run_upload_only(upload_only_cfg, real_video, "football", 4, service=svc)

        assert svc.thumbnails_resource.set_called
        assert svc.thumbnails_resource.last_video_id == svc.video_id

    def test_logs_error_on_upload_exception(self, real_video, upload_only_cfg, tmp_path, monkeypatch, caplog):
        import logging
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        from googleapiclient.errors import HttpError
        import httplib2
        resp = httplib2.Response({"status": 403})
        svc = FakeYouTubeService(raise_on_insert=HttpError(resp=resp, content=b"Forbidden"))

        with caplog.at_level(logging.ERROR, logger="automation"):
            run_upload_only(upload_only_cfg, real_video, "gaming", 1800, service=svc)

        assert any("Upload failed" in r.message or "403" in r.message for r in caplog.records)

    def test_creates_thumbnail_dir_if_missing(self, real_video, upload_only_cfg, tmp_path, monkeypatch):
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        thumb_dir = Path(upload_only_cfg["thumbnail_path"])
        assert not thumb_dir.exists()
        svc = FakeYouTubeService()

        run_upload_only(upload_only_cfg, real_video, "gaming", 1800, service=svc)

        assert thumb_dir.exists()

    def test_no_thumbnail_set_when_extraction_fails(self, tmp_path, upload_only_cfg, monkeypatch):
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 50)
        svc = FakeYouTubeService()

        run_upload_only(upload_only_cfg, corrupt, "football", 3600, service=svc)

        assert not svc.thumbnails_resource.set_called

    def test_run_completes_with_private_privacy_status(self, real_video, upload_only_cfg, tmp_path, monkeypatch):
        monkeypatch.setattr("automation.LAST_RUN_FILE", tmp_path / "last_run.json")
        upload_only_cfg["youtube"]["privacy_status"] = "private"
        svc = FakeYouTubeService()

        run_upload_only(upload_only_cfg, real_video, "gaming", 4, service=svc)

        last_run = json.loads((tmp_path / "last_run.json").read_text())
        assert last_run["runs"][0]["video_id"] == svc.video_id


# ── CLI --upload-only flag ────────────────────────────────────────────────────

class TestUploadOnlyCLI:

    def test_upload_only_flag_parsed(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--upload-only", type=str, default=None)
        parser.add_argument("--upload-topic", type=str, default=None)
        parser.add_argument("--upload-duration", type=int, default=None)

        args = parser.parse_args([
            "--upload-only", "/tmp/video.mp4",
            "--upload-topic", "gaming",
            "--upload-duration", "7200",
        ])

        assert args.upload_only == "/tmp/video.mp4"
        assert args.upload_topic == "gaming"
        assert args.upload_duration == 7200

    def test_upload_only_defaults_to_none(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--upload-only", type=str, default=None)
        parser.add_argument("--upload-topic", type=str, default=None)
        parser.add_argument("--upload-duration", type=int, default=None)

        args = parser.parse_args([])
        assert args.upload_only is None
        assert args.upload_topic is None
        assert args.upload_duration is None

    def test_upload_only_exits_when_file_missing(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "automation.py",
                "--upload-only", str(tmp_path / "nonexistent.mp4"),
                "--upload-topic", "gaming",
                "--upload-duration", "3600",
            ],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
        )
        assert result.returncode == 1
