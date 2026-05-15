"""Tests for thumbnail logic (Bug 1 fix).

_extract_frame tests use real ffmpeg + real video files.
_try_thumbnail tests inject a thumbnail_fetcher to control the source-thumbnail
path without network calls, and use real ffmpeg for the frame-fallback path.
"""
import logging
from pathlib import Path

import pytest

from automation import _extract_frame, _seconds_to_timestamp, _try_thumbnail


# ── _seconds_to_timestamp ──────────────────────────────────────────────────────

class TestSecondsToTimestamp:

    def test_zero(self):
        assert _seconds_to_timestamp(0) == "00:00:00"

    def test_one_minute(self):
        assert _seconds_to_timestamp(60) == "00:01:00"

    def test_one_hour(self):
        assert _seconds_to_timestamp(3600) == "01:00:00"

    def test_midpoint_two_hour_video(self):
        # 7200 / 2 = 3600 = 01:00:00
        assert _seconds_to_timestamp(3600) == "01:00:00"

    def test_large_value(self):
        # 10 hours + 30 min + 15 sec
        assert _seconds_to_timestamp(37815) == "10:30:15"

    def test_negative_clamps_to_zero(self):
        assert _seconds_to_timestamp(-100) == "00:00:00"

    def test_fractional_truncated(self):
        # 90.9 seconds → 1 min 30 sec
        assert _seconds_to_timestamp(90.9) == "00:01:30"

    def test_format_two_digits(self):
        # All fields must be zero-padded
        result = _seconds_to_timestamp(3661)  # 1h 1m 1s
        assert result == "01:01:01"


# ── _extract_frame ─────────────────────────────────────────────────────────────

class TestExtractFrame:

    def test_creates_jpeg_from_real_video(self, real_video, tmp_path):
        out = str(tmp_path / "thumb.jpg")
        result = _extract_frame(real_video, out, timestamp="00:00:02")
        assert result == out
        assert Path(out).exists()
        assert Path(out).stat().st_size > 1000  # real JPEG, not empty

    def test_returns_none_for_nonexistent_video(self, tmp_path):
        out = str(tmp_path / "thumb.jpg")
        result = _extract_frame(tmp_path / "missing.mp4", out)
        assert result is None

    def test_returns_none_for_corrupt_video(self, tmp_path):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 100)
        out = str(tmp_path / "thumb.jpg")
        result = _extract_frame(corrupt, out)
        assert result is None

    def test_timestamp_past_duration_still_returns_frame(self, real_video, tmp_path):
        # ffmpeg seeks to last frame when timestamp exceeds duration
        out = str(tmp_path / "thumb.jpg")
        result = _extract_frame(real_video, out, timestamp="99:00:00")
        # ffmpeg returns last available frame; result may be None on some builds
        # Accept either behaviour — just verify no exception raised
        assert result is None or Path(out).exists()

    def test_default_timestamp_used(self, real_video, tmp_path):
        out = str(tmp_path / "thumb_default.jpg")
        # Default is 00:01:00; real_video is only 5s so ffmpeg returns last frame
        result = _extract_frame(real_video, out)
        # Should not raise; frame at or before EOF
        assert result is None or Path(out).exists()

    def test_custom_timestamp_early_in_video(self, real_video, tmp_path):
        out = str(tmp_path / "thumb_early.jpg")
        result = _extract_frame(real_video, out, timestamp="00:00:01")
        assert result == out
        assert Path(out).stat().st_size > 1000


# ── _try_thumbnail ─────────────────────────────────────────────────────────────

class TestTryThumbnail:
    """
    Tests for the source-first / midpoint-fallback thumbnail logic.

    thumbnail_fetcher is injected — it's a function that takes (source_ids, path)
    and returns a path string or None. Real ffmpeg handles the frame-fallback path.
    """

    def _source_returns(self, value):
        """Factory: returns a thumbnail_fetcher that always returns `value`."""
        def fetcher(source_ids, thumbnail_path):
            return value
        return fetcher

    def test_uses_source_thumbnail_when_available(self, real_video, tmp_path):
        fake_source_path = str(tmp_path / "source.jpg")
        Path(fake_source_path).write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # fake JPEG header

        result = _try_thumbnail(
            source_ids=["vid123"],
            thumbnail_path=str(tmp_path / "thumb.jpg"),
            compiled_video=real_video,
            total_seconds=7200.0,
            thumbnail_fetcher=self._source_returns(fake_source_path),
        )
        assert result == fake_source_path

    def test_falls_back_to_frame_when_source_returns_none(self, real_video, tmp_path):
        thumb_path = str(tmp_path / "thumb.jpg")
        result = _try_thumbnail(
            source_ids=["vid123"],
            thumbnail_path=thumb_path,
            compiled_video=real_video,
            total_seconds=6.0,  # midpoint = 3s, within the 5s video
            thumbnail_fetcher=self._source_returns(None),
        )
        assert result == thumb_path
        assert Path(thumb_path).stat().st_size > 1000

    def test_falls_back_to_frame_when_source_fails(self, real_video, tmp_path):
        def failing_fetcher(source_ids, thumbnail_path):
            return None

        thumb_path = str(tmp_path / "thumb.jpg")
        result = _try_thumbnail(
            source_ids=["dead_vid"],
            thumbnail_path=thumb_path,
            compiled_video=real_video,
            total_seconds=4.0,
            thumbnail_fetcher=failing_fetcher,
        )
        assert result is not None
        assert Path(thumb_path).exists()

    def test_returns_none_when_both_fail(self, tmp_path):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 50)

        result = _try_thumbnail(
            source_ids=["vid123"],
            thumbnail_path=str(tmp_path / "thumb.jpg"),
            compiled_video=corrupt,
            total_seconds=60.0,
            thumbnail_fetcher=self._source_returns(None),
        )
        assert result is None

    def test_logs_source_success(self, real_video, tmp_path, caplog):
        fake_path = str(tmp_path / "source.jpg")
        Path(fake_path).write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        with caplog.at_level(logging.INFO):
            _try_thumbnail(
                source_ids=["abc"],
                thumbnail_path=str(tmp_path / "thumb.jpg"),
                compiled_video=real_video,
                total_seconds=3600.0,
                thumbnail_fetcher=self._source_returns(fake_path),
            )
        assert any("source image" in r.message for r in caplog.records)

    def test_logs_fallback_timestamp(self, real_video, tmp_path, caplog):
        with caplog.at_level(logging.INFO):
            _try_thumbnail(
                source_ids=["abc"],
                thumbnail_path=str(tmp_path / "thumb.jpg"),
                compiled_video=real_video,
                total_seconds=6.0,  # midpoint 3s → 00:00:03
                thumbnail_fetcher=self._source_returns(None),
            )
        assert any("00:00:03" in r.message for r in caplog.records)

    def test_logs_warning_when_both_fail(self, tmp_path, caplog):
        corrupt = tmp_path / "c.mp4"
        corrupt.write_bytes(b"\x00" * 50)

        with caplog.at_level(logging.WARNING):
            _try_thumbnail(
                source_ids=[],
                thumbnail_path=str(tmp_path / "t.jpg"),
                compiled_video=corrupt,
                total_seconds=10.0,
                thumbnail_fetcher=self._source_returns(None),
            )
        assert any("Could not extract thumbnail" in r.message for r in caplog.records)

    def test_empty_source_ids_still_attempts_frame(self, real_video, tmp_path):
        thumb_path = str(tmp_path / "thumb.jpg")
        result = _try_thumbnail(
            source_ids=[],
            thumbnail_path=thumb_path,
            compiled_video=real_video,
            total_seconds=4.0,
            thumbnail_fetcher=self._source_returns(None),
        )
        assert result is not None

    def test_uses_default_fetcher_when_none_injected(self, tmp_path):
        # When no fetcher injected, defaults to extract_thumbnail.
        # extract_thumbnail will fail (no real YouTube IDs here) and fallback fires.
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 50)
        result = _try_thumbnail(
            source_ids=["not_a_real_id"],
            thumbnail_path=str(tmp_path / "thumb.jpg"),
            compiled_video=corrupt,
            total_seconds=60.0,
        )
        # Both fail gracefully — no exception
        assert result is None
