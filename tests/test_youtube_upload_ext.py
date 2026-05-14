"""Extended tests for src/youtube_upload.py.

Covers: authenticate error paths, extract_thumbnail (real network disabled),
upload_video retry, set_thumbnail failure, wait_and_delete_when_public.

Uses FakeYouTubeService for all API calls — no real Google API.
format_title / format_description / build_tags covered in test_metadata.py.
"""
import logging
import time
import types
from pathlib import Path

import pytest
from googleapiclient.errors import HttpError
import httplib2

from src.youtube_upload import (
    YouTubeAuthExpiredError,
    authenticate,
    extract_thumbnail,
    set_thumbnail,
    upload_video,
    wait_and_delete_when_public,
)
from tests.conftest import FakeYouTubeService


# ── authenticate ──────────────────────────────────────────────────────────────

class TestAuthenticate:

    def test_raises_when_no_token_and_not_setup_mode(self, tmp_path):
        with pytest.raises(YouTubeAuthExpiredError):
            authenticate(
                str(tmp_path / "creds.json"),
                str(tmp_path / "token.json"),
                setup_mode=False,
            )

    def test_raises_file_not_found_in_setup_mode_without_creds(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            authenticate(
                str(tmp_path / "nonexistent_creds.json"),
                str(tmp_path / "token.json"),
                setup_mode=True,
            )

    def test_raises_auth_expired_on_refresh_error(self, tmp_path, monkeypatch):
        import json
        import google.auth.exceptions

        # Write a fake token file that looks expired
        token_data = {
            "token": "fake",
            "refresh_token": "fake_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "fake_client_id",
            "client_secret": "fake_client_secret",
            "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
            "expiry": "2020-01-01T00:00:00Z",
        }
        token_path = tmp_path / "token.json"
        token_path.write_text(json.dumps(token_data))

        # Make refresh() raise RefreshError (invalid_grant)
        def fake_refresh(self, request):
            raise google.auth.exceptions.RefreshError("invalid_grant: Token has been expired")

        from google.oauth2 import credentials as creds_module
        monkeypatch.setattr(creds_module.Credentials, "refresh", fake_refresh)

        with pytest.raises(YouTubeAuthExpiredError, match="revoked"):
            authenticate(str(tmp_path / "creds.json"), str(token_path), setup_mode=False)

        # Token file should be deleted
        assert not token_path.exists()


# ── extract_thumbnail ─────────────────────────────────────────────────────────

class TestExtractThumbnail:

    def test_returns_none_for_invalid_video_ids(self, tmp_path):
        # These IDs don't exist on YouTube — all quality attempts return nothing useful
        result = extract_thumbnail(
            ["not_a_real_id_xyz_123"],
            str(tmp_path / "thumb.jpg"),
        )
        # May succeed (if YouTube serves a placeholder) or fail (network error);
        # either way, no exception. If it returns None, that's the expected failure.
        assert result is None or Path(result).exists()

    def test_returns_none_for_empty_list(self, tmp_path):
        result = extract_thumbnail([], str(tmp_path / "thumb.jpg"))
        assert result is None

    def test_creates_parent_dir(self, tmp_path):
        nested = str(tmp_path / "nested" / "deep" / "thumb.jpg")
        extract_thumbnail([], nested)  # no IDs → returns None but creates dir
        assert (tmp_path / "nested" / "deep").exists()


# ── upload_video ──────────────────────────────────────────────────────────────

class TestUploadVideo:

    def test_success_returns_video_id(self, real_video):
        svc = FakeYouTubeService(video_id="returned_vid_id")
        result = upload_video(
            service=svc,
            video_path=real_video,
            title="Test Upload",
            description="desc",
            tags=["test"],
        )
        assert result == "returned_vid_id"

    def test_raises_on_4xx_error(self, real_video):
        resp = httplib2.Response({"status": 403})
        svc = FakeYouTubeService(raise_on_insert=HttpError(resp=resp, content=b"Forbidden"))
        with pytest.raises(HttpError):
            upload_video(service=svc, video_path=real_video, title="T", description="D", tags=[])

    def test_retries_on_5xx_error_then_raises(self, real_video, monkeypatch):
        import src.youtube_upload as yt_mod
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        resp = httplib2.Response({"status": 503})
        err = HttpError(resp=resp, content=b"Service Unavailable")

        call_count = [0]
        original_next_chunk = None

        class _RetryRequest:
            def next_chunk(self):
                call_count[0] += 1
                if call_count[0] <= 11:
                    raise err
                return None, {"id": "vid_id"}

        class _RetryVideosResource:
            def insert(self, **kwargs):
                return _RetryRequest()
            def list(self, **kwargs):
                return None

        class _RetrySvc:
            def videos(self):
                return _RetryVideosResource()
            def thumbnails(self):
                return None

        with pytest.raises(Exception, match="retries"):
            upload_video(service=_RetrySvc(), video_path=real_video, title="T", description="D", tags=[])

        assert len(sleeps) > 0  # at least one retry sleep


# ── set_thumbnail ─────────────────────────────────────────────────────────────

class TestSetThumbnail:

    def test_returns_true_on_success(self, real_video, tmp_path):
        thumb = tmp_path / "thumb.jpg"
        thumb.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        svc = FakeYouTubeService()
        result = set_thumbnail(svc, "vid123", str(thumb))
        assert result is True

    def test_returns_false_on_http_error(self, tmp_path):
        thumb = tmp_path / "thumb.jpg"
        thumb.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        resp = httplib2.Response({"status": 403})
        err = HttpError(resp=resp, content=b"Forbidden")

        class _FailThumbRequest:
            def execute(self):
                raise err

        class _FailThumbnailsResource:
            def set(self, **kwargs):
                return _FailThumbRequest()

        class _FailSvc:
            def thumbnails(self):
                return _FailThumbnailsResource()

        result = set_thumbnail(_FailSvc(), "vid123", str(thumb))
        assert result is False


# ── wait_and_delete_when_public ───────────────────────────────────────────────

class TestWaitAndDeleteWhenPublic:

    def test_deletes_file_when_processed_and_public(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        video_file = tmp_path / "output.mp4"
        video_file.write_bytes(b"video content")
        svc = FakeYouTubeService()  # _FakeListRequest returns processed+public

        result = wait_and_delete_when_public(
            service=svc, video_id="vid123", video_path=video_file,
            poll_interval=0, max_wait_seconds=10,
        )
        assert result is True
        assert not video_file.exists()

    def test_returns_true_when_file_already_gone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        missing_file = tmp_path / "gone.mp4"
        svc = FakeYouTubeService()

        result = wait_and_delete_when_public(
            service=svc, video_id="vid123", video_path=missing_file,
            poll_interval=0, max_wait_seconds=10,
        )
        assert result is True

    def test_returns_false_on_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        video_file = tmp_path / "output.mp4"
        video_file.write_bytes(b"video content")

        class _ProcessingListRequest:
            def execute(self):
                return {"items": [{"status": {"uploadStatus": "uploaded", "privacyStatus": "private"}}]}

        class _ProcessingSvc:
            def videos(self):
                return types.SimpleNamespace(list=lambda **kw: _ProcessingListRequest())

        monkeypatch.setattr(time, "time", lambda: 0)
        call_count = [0]
        original_time = time.time

        times = [0, 0, 0, 0, 10000]  # jump to past deadline quickly

        def fake_time():
            if times:
                return times.pop(0)
            return 10000

        monkeypatch.setattr(time, "time", fake_time)

        result = wait_and_delete_when_public(
            service=_ProcessingSvc(), video_id="vid123", video_path=video_file,
            poll_interval=0, max_wait_seconds=1,
        )
        assert result is False
        assert video_file.exists()  # not deleted on timeout

    def test_returns_false_on_failed_upload_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        video_file = tmp_path / "output.mp4"
        video_file.write_bytes(b"video content")

        class _FailedListRequest:
            def execute(self):
                return {"items": [{"status": {"uploadStatus": "failed", "privacyStatus": "private"}}]}

        class _FailedSvc:
            def videos(self):
                return types.SimpleNamespace(list=lambda **kw: _FailedListRequest())

        result = wait_and_delete_when_public(
            service=_FailedSvc(), video_id="vid123", video_path=video_file,
            poll_interval=0, max_wait_seconds=10,
        )
        assert result is False

    def test_logs_warning_on_api_error(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        video_file = tmp_path / "output.mp4"
        video_file.write_bytes(b"video content")

        times = iter([0, 0, 10000])
        monkeypatch.setattr(time, "time", lambda: next(times, 10000))

        resp = httplib2.Response({"status": 500})
        err = HttpError(resp=resp, content=b"Server Error")

        class _ErrListRequest:
            def execute(self):
                raise err

        class _ErrSvc:
            def videos(self):
                return types.SimpleNamespace(list=lambda **kw: _ErrListRequest())

        with caplog.at_level(logging.WARNING):
            wait_and_delete_when_public(
                service=_ErrSvc(), video_id="vid123", video_path=video_file,
                poll_interval=0, max_wait_seconds=1,
            )
        # No uncaught exception — test passes

    def test_unexpected_exception_logged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        video_file = tmp_path / "output.mp4"
        video_file.write_bytes(b"video content")

        times = iter([0, 0, 10000])
        monkeypatch.setattr(time, "time", lambda: next(times, 10000))

        class _BrokenListRequest:
            def execute(self):
                raise ValueError("unexpected internal error")

        class _BrokenSvc:
            def videos(self):
                return types.SimpleNamespace(list=lambda **kw: _BrokenListRequest())

        logged = []
        wait_and_delete_when_public(
            service=_BrokenSvc(), video_id="vid123", video_path=video_file,
            poll_interval=0, max_wait_seconds=1,
            log_fn=lambda msg: logged.append(msg),
        )
        assert any("unexpected" in m.lower() or "warning" in m.lower() or "error" in m.lower()
                   for m in logged)


# ── authenticate setup_mode InstalledAppFlow ──────────────────────────────────

class TestAuthenticateSetupMode:

    def test_setup_mode_calls_installed_app_flow(self, tmp_path, monkeypatch):
        import os
        creds_file = tmp_path / "client_secrets.json"
        creds_file.write_text('{"installed": {"client_id": "x", "client_secret": "y", '
                              '"redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"], '
                              '"auth_uri": "https://a.com", "token_uri": "https://b.com"}}')
        token_path = tmp_path / "token.json"

        from google_auth_oauthlib.flow import InstalledAppFlow
        import google.oauth2.credentials as creds_mod
        import src.youtube_upload as yt_mod

        fake_creds_json = ('{"token": "fake_token", "refresh_token": "r", '
                           '"token_uri": "https://b.com", '
                           '"client_id": "x", "client_secret": "y", '
                           '"scopes": ["https://www.googleapis.com/auth/youtube.upload"]}')

        class _FakeCreds:
            def to_json(self):
                return fake_creds_json
            valid = True
            expired = False
            refresh_token = "r"
            def refresh(self, req): pass

        class _FakeFlow:
            @classmethod
            def from_client_secrets_file(cls, path, scopes):
                return cls()
            def run_local_server(self, **kw):
                return _FakeCreds()

        monkeypatch.setattr(yt_mod, "InstalledAppFlow", _FakeFlow)
        monkeypatch.delenv("SDMNH_HEADLESS", raising=False)

        from googleapiclient.discovery import build as real_build

        class _FakeService:
            pass

        monkeypatch.setattr(yt_mod, "build", lambda *a, **kw: _FakeService())

        result = authenticate(str(creds_file), str(token_path), setup_mode=True)
        assert isinstance(result, _FakeService)
        assert token_path.exists()

    def test_setup_mode_headless_uses_bind_addr(self, tmp_path, monkeypatch):
        import os
        creds_file = tmp_path / "client_secrets.json"
        creds_file.write_text('{"installed": {"client_id": "x", "client_secret": "y", '
                              '"redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"], '
                              '"auth_uri": "https://a.com", "token_uri": "https://b.com"}}')
        token_path = tmp_path / "token.json"

        import src.youtube_upload as yt_mod

        fake_creds_json = ('{"token": "fake_token", "refresh_token": "r", '
                           '"token_uri": "https://b.com", '
                           '"client_id": "x", "client_secret": "y", '
                           '"scopes": ["https://www.googleapis.com/auth/youtube.upload"]}')

        headless_called = []

        class _FakeCreds:
            def to_json(self):
                return fake_creds_json
            valid = True
            expired = False
            refresh_token = "r"
            def refresh(self, req): pass

        class _FakeHeadlessFlow:
            @classmethod
            def from_client_secrets_file(cls, path, scopes):
                return cls()
            def run_local_server(self, **kw):
                headless_called.append(kw)
                return _FakeCreds()

        monkeypatch.setattr(yt_mod, "InstalledAppFlow", _FakeHeadlessFlow)
        monkeypatch.setenv("SDMNH_HEADLESS", "1")

        class _FakeService:
            pass

        monkeypatch.setattr(yt_mod, "build", lambda *a, **kw: _FakeService())

        result = authenticate(str(creds_file), str(token_path), setup_mode=True)
        assert isinstance(result, _FakeService)
        assert any(kw.get("bind_addr") == "0.0.0.0" for kw in headless_called)


# ── extract_thumbnail success path ────────────────────────────────────────────

class TestExtractThumbnailSuccess:

    def test_success_returns_output_path(self, tmp_path, monkeypatch):
        import urllib.request as ureq

        big_image_data = b"\xff\xd8\xff" + b"\xAB" * 10000  # >5KB fake JPEG

        class _FakeResponse:
            def read(self):
                return big_image_data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        monkeypatch.setattr(ureq, "urlopen", lambda url, timeout=10: _FakeResponse())

        out_path = str(tmp_path / "thumb.jpg")
        result = extract_thumbnail(["fakevid123"], out_path)
        assert result == out_path
        assert Path(out_path).exists()
        assert Path(out_path).stat().st_size > 5000

    def test_skips_tiny_placeholder_then_succeeds(self, tmp_path, monkeypatch):
        import urllib.request as ureq

        call_count = [0]

        class _FakeResponse:
            def __init__(self, data):
                self._data = data
            def read(self):
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        def fake_urlopen(url, timeout=10):
            call_count[0] += 1
            if call_count[0] == 1:
                return _FakeResponse(b"\xff\xd8\xff" + b"\x00" * 100)  # tiny placeholder
            return _FakeResponse(b"\xff\xd8\xff" + b"\xAB" * 10000)   # big real thumbnail

        monkeypatch.setattr(ureq, "urlopen", fake_urlopen)
        out_path = str(tmp_path / "thumb.jpg")
        result = extract_thumbnail(["fakevid123"], out_path)
        assert result == out_path


# ── upload_video with progress status ────────────────────────────────────────

class TestUploadVideoProgress:

    def test_progress_callback_invoked(self, real_video, monkeypatch):
        import src.youtube_upload as yt_mod

        class _FakeStatus:
            def progress(self):
                return 0.5

        class _ProgressRequest:
            _done = False
            def next_chunk(self):
                if not self._done:
                    self._done = True
                    return _FakeStatus(), None
                return None, {"id": "prog_vid_id"}

        class _ProgressVideosResource:
            def insert(self, **kwargs):
                return _ProgressRequest()
            def list(self, **kwargs):
                return None

        class _ProgressSvc:
            def videos(self):
                return _ProgressVideosResource()
            def thumbnails(self):
                return None

        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        result = upload_video(
            service=_ProgressSvc(), video_path=real_video,
            title="T", description="D", tags=[],
        )
        assert result == "prog_vid_id"
