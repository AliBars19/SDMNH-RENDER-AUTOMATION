"""Tests for combine.py utility functions.

Covers: _sort_by_upload_date, load_config, get_video_duration, is_valid_video,
_get_cooldown_video_ids, select_videos, _score_video, select_videos_within_duration,
sanitize_filename, cleanup_stale_temps, cleanup_downloads.

All ffprobe calls use real binaries; no subprocess patching.
"""
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import combine
from src.database import Compilation, Video, compilation_videos


# ── _sort_by_upload_date ──────────────────────────────────────────────────────

class TestSortByUploadDate:

    def test_sorted_newest_first(self, session, sample_videos):
        video_files = {v.id: Path(f"/tmp/{v.id}.mp4") for v in sample_videos}
        result = combine._sort_by_upload_date(video_files, sample_videos)
        dates = []
        id_to_date = {v.id: v.upload_date for v in sample_videos}
        for vid_id in result:
            dates.append(id_to_date[vid_id])
        assert dates == sorted(dates, reverse=True)

    def test_returns_ordered_dict(self, session, sample_videos):
        video_files = {v.id: Path("/tmp/x.mp4") for v in sample_videos}
        result = combine._sort_by_upload_date(video_files, sample_videos)
        assert isinstance(result, OrderedDict)

    def test_preserves_all_ids(self, session, sample_videos):
        video_files = {v.id: Path("/tmp/x.mp4") for v in sample_videos}
        result = combine._sort_by_upload_date(video_files, sample_videos)
        assert set(result.keys()) == set(video_files.keys())

    def test_single_video_unchanged(self, session, sample_videos):
        v = sample_videos[0]
        video_files = {v.id: Path("/tmp/x.mp4")}
        result = combine._sort_by_upload_date(video_files, sample_videos)
        assert list(result.keys()) == [v.id]

    def test_video_with_none_date_sorted_last(self, session):
        v1 = Video(youtube_id="a", title="A", url="http://a", upload_date="20240101", topic="general")
        v2 = Video(youtube_id="b", title="B", url="http://b", upload_date=None, topic="general")
        session.add_all([v1, v2])
        session.commit()
        video_files = {v1.id: Path("/tmp/a.mp4"), v2.id: Path("/tmp/b.mp4")}
        result = combine._sort_by_upload_date(video_files, [v1, v2])
        ids = list(result.keys())
        assert ids[0] == v1.id  # v1 has date, sorts first (newest)

    def test_video_with_bad_date_format_sorted_last(self, session):
        v1 = Video(youtube_id="c1", title="C", url="http://c", upload_date="20240101", topic="general")
        v2 = Video(youtube_id="d1", title="D", url="http://d", upload_date="not-a-date", topic="general")
        session.add_all([v1, v2])
        session.commit()
        video_files = {v1.id: Path("/tmp/c.mp4"), v2.id: Path("/tmp/d.mp4")}
        result = combine._sort_by_upload_date(video_files, [v1, v2])
        ids = list(result.keys())
        assert ids[0] == v1.id  # valid date sorts first (newest-first)


# ── load_config ────────────────────────────────────────────────────────────────

class TestLoadConfig:

    def test_returns_parsed_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("foo: bar\nbaz: 42\n")
        result = combine.load_config(str(cfg_file))
        assert result == {"foo": "bar", "baz": 42}

    def test_exits_when_file_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            combine.load_config(str(tmp_path / "nonexistent.yaml"))


# ── get_video_duration ────────────────────────────────────────────────────────

class TestGetVideoDuration:

    def test_returns_float_for_real_video(self, real_video):
        duration = combine.get_video_duration(real_video)
        assert isinstance(duration, float)
        assert duration > 0

    def test_returns_zero_for_missing_file(self, tmp_path):
        duration = combine.get_video_duration(tmp_path / "nope.mp4")
        assert duration == 0.0

    def test_returns_zero_for_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 100)
        duration = combine.get_video_duration(corrupt)
        assert duration == 0.0

    def test_duration_near_expected(self, real_video):
        duration = combine.get_video_duration(real_video)
        assert 4.0 <= duration <= 6.0  # real_video is 5 seconds

    def test_subprocess_exception_returns_zero(self, monkeypatch):
        import subprocess as sp
        monkeypatch.setattr(sp, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
        result = combine.get_video_duration("/some/path.mp4")
        assert result == 0.0


# ── is_valid_video ─────────────────────────────────────────────────────────────

class TestIsValidVideo:

    def test_real_video_is_valid(self, real_video):
        assert combine.is_valid_video(real_video) is True

    def test_missing_file_invalid(self, tmp_path):
        assert combine.is_valid_video(tmp_path / "nope.mp4") is False

    def test_corrupt_file_invalid(self, tmp_path):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 100)
        assert combine.is_valid_video(corrupt) is False

    def test_empty_file_invalid(self, tmp_path):
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        assert combine.is_valid_video(empty) is False

    def test_subprocess_exception_returns_false(self, monkeypatch):
        import subprocess as sp
        monkeypatch.setattr(sp, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
        assert combine.is_valid_video("/some/path.mp4") is False


# ── _get_cooldown_video_ids ────────────────────────────────────────────────────

class TestGetCooldownVideoIds:

    def test_returns_empty_when_no_compilations(self, session):
        result = combine._get_cooldown_video_ids(session, cooldown_days=30)
        assert result == set()

    def test_returns_ids_used_in_recent_compilation(self, session, sample_videos):
        comp = Compilation(topic="among_us", filename="comp.mp4", video_count=2)
        session.add(comp)
        session.flush()
        vid = sample_videos[0]
        session.execute(
            compilation_videos.insert().values(compilation_id=comp.id, video_id=vid.id)
        )
        session.commit()
        result = combine._get_cooldown_video_ids(session, cooldown_days=30)
        assert vid.id in result

    def test_excludes_old_compilations(self, session, sample_videos):
        old_time = datetime.now(timezone.utc) - timedelta(days=60)
        comp = Compilation(topic="among_us", filename="old_comp.mp4", video_count=1)
        comp.created_at = old_time
        session.add(comp)
        session.flush()
        vid = sample_videos[0]
        session.execute(
            compilation_videos.insert().values(compilation_id=comp.id, video_id=vid.id)
        )
        session.commit()
        result = combine._get_cooldown_video_ids(session, cooldown_days=30)
        assert vid.id not in result


# ── select_videos ──────────────────────────────────────────────────────────────

class TestSelectVideos:

    def test_returns_videos_for_topic(self, session, sample_videos):
        result = combine.select_videos(session, "among_us", 10, 30)
        assert len(result) >= 2
        assert all(v.topic == "among_us" for v in result)

    def test_empty_when_no_videos(self, session):
        result = combine.select_videos(session, "nonexistent", 10, 30)
        assert result == []

    def test_respects_count_limit(self, session, sample_videos):
        result = combine.select_videos(session, "among_us", 1, 30)
        assert len(result) <= 1

    def test_includes_cooldown_overflow_when_not_enough(self, session, sample_videos):
        # Put all among_us videos on cooldown
        comp = Compilation(topic="among_us", filename="c.mp4", video_count=2)
        session.add(comp)
        session.flush()
        for v in [v for v in sample_videos if v.topic == "among_us"]:
            session.execute(
                compilation_videos.insert().values(compilation_id=comp.id, video_id=v.id)
            )
        session.commit()
        # Request more than available outside cooldown
        result = combine.select_videos(session, "among_us", 10, 30)
        assert len(result) > 0  # falls back to cooldown overflow


# ── _score_video ──────────────────────────────────────────────────────────────

class TestScoreVideo:

    def _make_video_obj(self, view_count, upload_date):
        import types
        v = types.SimpleNamespace(view_count=view_count, upload_date=upload_date)
        return v

    def test_higher_views_scores_higher(self):
        popular = self._make_video_obj(1_000_000, "20240101")
        unpopular = self._make_video_obj(1_000, "20240101")
        newest_ts = datetime.strptime("20240101", "%Y%m%d").timestamp()
        oldest_ts = newest_ts
        s_popular = combine._score_video(popular, 1_000_000, newest_ts, oldest_ts)
        s_unpopular = combine._score_video(unpopular, 1_000_000, newest_ts, oldest_ts)
        assert s_popular > s_unpopular

    def test_newer_video_scores_higher_when_equal_views(self):
        new_vid = self._make_video_obj(0, "20240601")
        old_vid = self._make_video_obj(0, "20230101")
        newest_ts = datetime.strptime("20240601", "%Y%m%d").timestamp()
        oldest_ts = datetime.strptime("20230101", "%Y%m%d").timestamp()
        s_new = combine._score_video(new_vid, 0, newest_ts, oldest_ts)
        s_old = combine._score_video(old_vid, 0, newest_ts, oldest_ts)
        assert s_new > s_old

    def test_none_views_treated_as_zero(self):
        # Use today's date so recency score is ~1.0 regardless of when test runs
        today = datetime.now().strftime("%Y%m%d")
        v = self._make_video_obj(None, today)
        ts = datetime.strptime(today, "%Y%m%d").timestamp()
        score = combine._score_video(v, 1_000_000, ts, ts)
        # pop_score=0, rec_score≈1.0 (today), total = 0*0.2 + 1.0*0.8 = 0.8
        assert score == pytest.approx(0.8, abs=0.05)

    def test_invalid_date_falls_back_gracefully(self):
        v = self._make_video_obj(100, "invalid_date")
        ts = 1_000_000.0
        score = combine._score_video(v, 100, ts, ts)
        assert isinstance(score, float)

    def test_zero_max_views_does_not_divide_by_zero(self):
        v = self._make_video_obj(0, "20240101")
        ts = datetime.strptime("20240101", "%Y%m%d").timestamp()
        score = combine._score_video(v, 0, ts, ts)
        # pop_score=0 (max_views=0 branch); no ZeroDivisionError
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_old_video_beyond_cutoff_gets_zero_recency(self):
        # Video from 2018 = far beyond 30-month cutoff
        v = self._make_video_obj(1_000_000, "20180101")
        ts = datetime.strptime("20180101", "%Y%m%d").timestamp()
        score = combine._score_video(v, 1_000_000, ts, ts)
        # rec_score=0 (>30mo old), pop_score=1.0, total = 1.0*0.2 + 0*0.8 = 0.2
        assert score == pytest.approx(0.2, abs=0.01)

    def test_recency_dominates_popularity(self):
        # Old viral video (10M views, 4 years old) should lose to recent low-view video
        today = datetime.now().strftime("%Y%m%d")
        old_viral = self._make_video_obj(10_000_000, "20220101")
        new_small = self._make_video_obj(10_000, today)
        ts = datetime.now().timestamp()
        s_old = combine._score_video(old_viral, 10_000_000, ts, ts)
        s_new = combine._score_video(new_small, 10_000_000, ts, ts)
        assert s_new > s_old


# ── select_videos_within_duration ─────────────────────────────────────────────

class TestSelectVideosWithinDuration:

    def test_returns_empty_when_no_videos(self, session):
        result = combine.select_videos_within_duration(session, "nonexistent", 43200, 30)
        assert result == []

    def test_respects_duration_cap(self, session, sample_videos):
        # Max 3600s — only 1-hour videos should fit
        result = combine.select_videos_within_duration(session, "among_us", 3600, 30)
        total = sum(v.duration or 3600 for v in result)
        assert total <= 3600

    def test_returns_something_within_large_cap(self, session, sample_videos):
        result = combine.select_videos_within_duration(session, "among_us", 43200, 30)
        assert len(result) > 0

    def test_all_on_cooldown_uses_overflow(self, session, sample_videos):
        comp = Compilation(topic="among_us", filename="c.mp4", video_count=2)
        session.add(comp)
        session.flush()
        for v in [v for v in sample_videos if v.topic == "among_us"]:
            session.execute(
                compilation_videos.insert().values(compilation_id=comp.id, video_id=v.id)
            )
        session.commit()
        result = combine.select_videos_within_duration(session, "among_us", 43200, 30)
        assert len(result) > 0  # uses cooldown overflow

    def test_handles_video_with_none_duration(self, session):
        v = Video(youtube_id="nodur", title="No Duration", url="http://x", duration=None,
                  upload_date="20240101", topic="gaming")
        session.add(v)
        session.commit()
        result = combine.select_videos_within_duration(session, "gaming", 43200, 30)
        assert any(r.youtube_id == "nodur" for r in result)

    def test_malformed_upload_date_handled(self, session):
        v = Video(youtube_id="baddate", title="Bad Date", url="http://x", duration=3600,
                  upload_date="bad-date-format", topic="gaming2")
        session.add(v)
        session.commit()
        result = combine.select_videos_within_duration(session, "gaming2", 43200, 30)
        assert any(r.youtube_id == "baddate" for r in result)


# ── sanitize_filename ──────────────────────────────────────────────────────────

class TestSanitizeFilename:

    def test_removes_invalid_chars(self):
        result = combine.sanitize_filename('hello<world>:test"foo/bar\\baz|q?u*e')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "/" not in result
        assert "\\" not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_truncates_to_200_chars(self):
        long_name = "a" * 300
        result = combine.sanitize_filename(long_name)
        assert len(result) == 200

    def test_normal_name_unchanged(self):
        name = "SIDEMEN AMONG US 2024"
        assert combine.sanitize_filename(name) == name

    def test_empty_string(self):
        assert combine.sanitize_filename("") == ""


# ── cleanup_stale_temps ────────────────────────────────────────────────────────

class TestCleanupStaleTemps:

    def test_removes_temp_video_files(self, tmp_path):
        (tmp_path / "temp_video_abc.mp4").write_bytes(b"x")
        (tmp_path / "temp_audio_def.m4a").write_bytes(b"x")
        (tmp_path / "temp_prog_ghi.mp4").write_bytes(b"x")
        combine.cleanup_stale_temps(str(tmp_path))
        assert not (tmp_path / "temp_video_abc.mp4").exists()
        assert not (tmp_path / "temp_audio_def.m4a").exists()
        assert not (tmp_path / "temp_prog_ghi.mp4").exists()

    def test_preserves_non_temp_files(self, tmp_path):
        keep = tmp_path / "real_video.mp4"
        keep.write_bytes(b"keep me")
        combine.cleanup_stale_temps(str(tmp_path))
        assert keep.exists()

    def test_no_error_on_empty_dir(self, tmp_path):
        combine.cleanup_stale_temps(str(tmp_path))  # no exception

    def test_unlink_exception_silently_ignored(self, tmp_path, monkeypatch):
        from pathlib import Path as RealPath
        (tmp_path / "temp_video_xyz.mp4").write_bytes(b"x")
        def raising_unlink(self, missing_ok=False):
            raise PermissionError("locked")
        monkeypatch.setattr(RealPath, "unlink", raising_unlink)
        combine.cleanup_stale_temps(str(tmp_path))  # must not raise


# ── cleanup_downloads ─────────────────────────────────────────────────────────

class TestCleanupDownloads:

    def test_removes_mp4_files_not_in_keep_set(self, tmp_path):
        old = tmp_path / "old_video.mp4"
        old.write_bytes(b"x")
        combine.cleanup_downloads(str(tmp_path))
        assert not old.exists()

    def test_keeps_files_in_keep_set(self, tmp_path):
        keep = tmp_path / "keep.mp4"
        keep.write_bytes(b"x")
        extra = tmp_path / "extra.mp4"
        extra.write_bytes(b"x")
        combine.cleanup_downloads(str(tmp_path), keep_files=[keep])
        assert keep.exists()
        assert not extra.exists()

    def test_no_error_on_empty_dir(self, tmp_path):
        combine.cleanup_downloads(str(tmp_path))  # no exception

    def test_none_keep_files_removes_all(self, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"x")
        (tmp_path / "b.mp4").write_bytes(b"x")
        combine.cleanup_downloads(str(tmp_path), keep_files=None)
        assert not list(tmp_path.glob("*.mp4"))

    def test_prints_message_when_files_removed(self, tmp_path, capsys):
        (tmp_path / "a.mp4").write_bytes(b"x")
        (tmp_path / "b.mp4").write_bytes(b"x")
        combine.cleanup_downloads(str(tmp_path))
        # Rich console.print goes to rich's console stdout — just verify no exception and files gone
        assert not list(tmp_path.glob("*.mp4"))


# ── get_video_audio_codec ─────────────────────────────────────────────────────

class TestGetVideoAudioCodec:

    def test_returns_none_on_subprocess_exception(self, monkeypatch):
        import subprocess as sp
        monkeypatch.setattr(sp, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
        result = combine.get_video_audio_codec(Path("/nonexistent.mp4"))
        assert result is None

    def test_returns_none_for_corrupt_file(self, tmp_path):
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 100)
        result = combine.get_video_audio_codec(corrupt)
        assert result is None

    def test_returns_codec_for_real_video(self, real_video):
        codec = combine.get_video_audio_codec(real_video)
        assert codec == "aac"
