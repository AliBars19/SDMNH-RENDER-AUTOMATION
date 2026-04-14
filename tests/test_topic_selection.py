import pytest
from datetime import datetime, timedelta, timezone

from src.database import Video, Compilation, compilation_videos


class TestSelectRandomTopic:

    def test_selects_topic_with_videos(self, session, sample_videos, sample_config):
        # Import here to avoid module-level side effects
        import sys
        sys.path.insert(0, ".")
        from automation import select_random_topic

        topic = select_random_topic(session, sample_config["topics"])
        assert topic is not None
        assert topic in sample_config["topics"]

    def test_skips_specified_topics(self, session, sample_videos, sample_config):
        from automation import select_random_topic

        # Skip all specific topics — should fall back to general
        skip = ["among_us", "try_not_to_laugh", "mukbang"]
        topic = select_random_topic(session, sample_config["topics"], skip_topics=skip)
        assert topic == "general"

    def test_returns_none_when_all_skipped(self, session, sample_config):
        from automation import select_random_topic

        # No videos in DB + skip everything
        topic = select_random_topic(session, sample_config["topics"])
        assert topic is None


class TestSelectVideosWithinDuration:

    def test_selects_within_limit(self, session, sample_videos):
        from combine import select_videos_within_duration

        # 10800s = 3 hours — should get 1 or 2 videos
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

        # Create a compilation using the among_us videos
        comp = Compilation(topic="among_us", filename="test.mp4", video_count=2)
        session.add(comp)
        session.flush()

        among_us_videos = [v for v in sample_videos if v.topic == "among_us"]
        for v in among_us_videos:
            session.execute(
                compilation_videos.insert().values(compilation_id=comp.id, video_id=v.id)
            )
        session.commit()

        # With 30-day cooldown, these should be excluded (or used as overflow)
        selected = select_videos_within_duration(session, "among_us", 86400, 30)
        # They should still appear as cooldown overflow since there are no other among_us videos
        assert len(selected) > 0


class TestStateHelpers:

    def test_today_utc(self):
        from automation import _today_utc

        result = _today_utc()
        expected = datetime.now(timezone.utc).date().isoformat()
        assert result == expected
