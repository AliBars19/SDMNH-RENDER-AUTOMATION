import pytest
from datetime import datetime, timedelta, timezone

from src.database import Video, Compilation, compilation_videos


class TestSelectTopicByRank:

    def test_selects_topic_with_videos(self, session, sample_videos, sample_config):
        import sys
        sys.path.insert(0, ".")
        from automation import select_topic_by_rank

        topic = select_topic_by_rank(session, sample_config["topics"])
        assert topic is not None
        assert topic in sample_config["topics"]

    def test_prefers_highest_total_views(self, session, sample_videos, sample_config):
        from automation import select_topic_by_rank

        # among_us: vid001 (15M) + vid005 (20M) = 35M
        # try_not_to_laugh: vid002 (8M)
        # mukbang: vid003 (5M)
        topic = select_topic_by_rank(session, sample_config["topics"])
        assert topic == "among_us"

    def test_skips_specified_topics(self, session, sample_videos, sample_config):
        from automation import select_topic_by_rank

        # Skip among_us (highest) → next is try_not_to_laugh (8M)
        topic = select_topic_by_rank(
            session, sample_config["topics"], skip_topics=["among_us"]
        )
        assert topic == "try_not_to_laugh"

    def test_falls_back_to_general(self, session, sample_videos, sample_config):
        from automation import select_topic_by_rank

        # Skip all specific topics
        skip = ["among_us", "try_not_to_laugh", "mukbang"]
        topic = select_topic_by_rank(session, sample_config["topics"], skip_topics=skip)
        assert topic == "general"

    def test_returns_none_when_all_skipped(self, session, sample_config):
        from automation import select_topic_by_rank

        # No videos in DB at all
        topic = select_topic_by_rank(session, sample_config["topics"])
        assert topic is None

    def test_weekly_cap_excludes_overused_topic(self, session, sample_videos, sample_config):
        from automation import select_topic_by_rank

        # Add 2 compilations for among_us this week → it should be capped
        for i in range(2):
            comp = Compilation(
                topic="among_us",
                filename=f"test_{i}.mp4",
                video_count=1,
                created_at=datetime.now(timezone.utc),
            )
            session.add(comp)
        session.commit()

        topic = select_topic_by_rank(
            session, sample_config["topics"], max_uses_per_week=2
        )
        # among_us capped → next is try_not_to_laugh
        assert topic == "try_not_to_laugh"

    def test_old_compilations_dont_count_toward_weekly_cap(self, session, sample_videos, sample_config):
        from automation import select_topic_by_rank

        # Add 2 compilations for among_us from last week → should NOT count
        last_week = datetime.now(timezone.utc) - timedelta(days=8)
        for i in range(2):
            comp = Compilation(
                topic="among_us",
                filename=f"old_{i}.mp4",
                video_count=1,
                created_at=last_week,
            )
            session.add(comp)
        session.commit()

        topic = select_topic_by_rank(
            session, sample_config["topics"], max_uses_per_week=2
        )
        # Last week's compilations don't count → among_us still available
        assert topic == "among_us"


# Keep backward-compat alias tests
class TestSelectRandomTopic:

    def test_alias_works(self, session, sample_videos, sample_config):
        from automation import select_random_topic

        topic = select_random_topic(session, sample_config["topics"])
        assert topic is not None
        assert topic in sample_config["topics"]

    def test_skips_specified_topics(self, session, sample_videos, sample_config):
        from automation import select_random_topic

        skip = ["among_us", "try_not_to_laugh", "mukbang"]
        topic = select_random_topic(session, sample_config["topics"], skip_topics=skip)
        assert topic == "general"

    def test_returns_none_when_all_skipped(self, session, sample_config):
        from automation import select_random_topic

        topic = select_random_topic(session, sample_config["topics"])
        assert topic is None


class TestSelectVideosWithinDuration:

    def test_selects_within_limit(self, session, sample_videos):
        from combine import select_videos_within_duration

        selected = select_videos_within_duration(session, "among_us", 10800, 30)
        total_duration = sum(v.duration for v in selected)
        assert total_duration <= 10800
        assert len(selected) > 0

    def test_respects_topic_filter(self, session, sample_videos):
        from combine import select_videos_within_duration

        selected = select_videos_within_duration(session, "mukbang", 86400, 30)
        for v in selected:
            assert v.topic == "mukbang"

    def test_empty_topic_returns_empty(self, session, sample_videos):
        from combine import select_videos_within_duration

        selected = select_videos_within_duration(session, "nonexistent_topic", 86400, 30)
        assert selected == []

    def test_cooldown_excludes_recent(self, session, sample_videos):
        from combine import select_videos_within_duration

        comp = Compilation(topic="among_us", filename="test.mp4", video_count=2)
        session.add(comp)
        session.flush()

        among_us_videos = [v for v in sample_videos if v.topic == "among_us"]
        for v in among_us_videos:
            session.execute(
                compilation_videos.insert().values(compilation_id=comp.id, video_id=v.id)
            )
        session.commit()

        selected = select_videos_within_duration(session, "among_us", 86400, 30)
        assert len(selected) > 0


class TestVideoScoring:

    def test_high_views_scores_higher(self, session, sample_videos):
        from combine import _score_video

        max_views = 20000000
        newest_ts = 1714521600.0
        oldest_ts = 1705276800.0

        v5 = [v for v in sample_videos if v.youtube_id == "vid005"][0]
        v1 = [v for v in sample_videos if v.youtube_id == "vid001"][0]

        score_v5 = _score_video(v5, max_views, newest_ts, oldest_ts)
        score_v1 = _score_video(v1, max_views, newest_ts, oldest_ts)
        assert score_v5 > score_v1

    def test_selection_prefers_popular_recent(self, session, sample_videos):
        from combine import select_videos_within_duration

        selected = select_videos_within_duration(session, "among_us", 4200, 30)
        assert len(selected) == 1
        assert selected[0].youtube_id == "vid005"


class TestStateHelpers:

    def test_today_utc(self):
        from automation import _today_utc

        result = _today_utc()
        expected = datetime.now(timezone.utc).date().isoformat()
        assert result == expected

    def test_already_ran_today_false_when_no_runs(self, tmp_path, monkeypatch):
        from automation import already_ran_today
        import automation

        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "last_run.json")
        assert already_ran_today() is False

    def test_already_ran_today_true_after_two_successes(self, tmp_path, monkeypatch):
        import json
        from automation import already_ran_today, _today_utc
        import automation

        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "last_run.json")
        state = {
            "date": _today_utc(),
            "runs": [
                {"topic": "football", "video_id": "aaa"},
                {"topic": "gaming", "video_id": "bbb"},
            ],
            "failed_topics": [],
        }
        (tmp_path / "last_run.json").write_text(json.dumps(state))
        assert already_ran_today() is True

    def test_already_ran_today_false_after_one_success(self, tmp_path, monkeypatch):
        import json
        from automation import already_ran_today, _today_utc
        import automation

        monkeypatch.setattr(automation, "LAST_RUN_FILE", tmp_path / "last_run.json")
        state = {
            "date": _today_utc(),
            "runs": [{"topic": "football", "video_id": "aaa"}],
            "failed_topics": [],
        }
        (tmp_path / "last_run.json").write_text(json.dumps(state))
        assert already_ran_today() is False
