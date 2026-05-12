import pytest

from src.youtube_upload import build_tags, format_description, format_title


@pytest.fixture
def display_names() -> dict[str, str]:
    return {
        "among_us": "AMONG US",
        "try_not_to_laugh": "TRY NOT TO LAUGH",
        "mukbang": "MUKBANG",
        "quiz": "QUIZ",
        "general": "COMPILATION",
    }


@pytest.fixture
def topic_tags() -> dict[str, list[str]]:
    return {
        "among_us": ["among us", "among us sidemen"],
        "quiz": ["quiz", "trivia"],
    }


class TestFormatTitle:

    def test_basic_title(self, display_names):
        result = format_title("among_us", 7200, display_names)
        assert result == "SIDEMEN AMONG US - 2 HOUR SPECIAL"

    def test_rounds_hours(self, display_names):
        result = format_title("mukbang", 5400, display_names)  # 1.5 hours
        assert result == "SIDEMEN MUKBANG - 1.5 HOUR SPECIAL"

    def test_minimum_one_hour(self, display_names):
        result = format_title("quiz", 600, display_names)  # 10 minutes
        assert result == "SIDEMEN QUIZ - 1 HOUR SPECIAL"

    def test_large_duration(self, display_names):
        result = format_title("try_not_to_laugh", 43200, display_names)  # 12 hours
        assert result == "SIDEMEN TRY NOT TO LAUGH - 12 HOUR SPECIAL"

    def test_unknown_topic_fallback(self, display_names):
        result = format_title("unknown_topic", 3600, display_names)
        assert "UNKNOWN TOPIC" in result

    def test_general_topic(self, display_names):
        result = format_title("general", 3600, display_names)
        assert result == "SIDEMEN COMPILATION - 1 HOUR SPECIAL"

    def test_custom_title_format(self, display_names):
        result = format_title(
            "quiz", 3600, display_names, title_format="{topic} ({hours}h)"
        )
        assert result == "QUIZ (1h)"


class TestFormatDescription:

    def test_template_substitution(self, display_names):
        template = "Best {topic} moments! #{topic_tag}"
        result = format_description("among_us", template, display_names)
        assert "AMONG US" in result
        assert "#amongus" in result

    def test_empty_template(self, display_names):
        result = format_description("quiz", "", display_names)
        assert result == ""


class TestBuildTags:

    def test_combines_base_and_topic_tags(self, topic_tags):
        base = ["sidemen", "compilation"]
        result = build_tags("among_us", base, topic_tags)
        assert "sidemen" in result
        assert "compilation" in result
        assert "among us" in result

    def test_no_duplicates(self, topic_tags):
        base = ["sidemen", "among us"]
        result = build_tags("among_us", base, topic_tags)
        assert result.count("among us") == 1

    def test_max_500_tags(self, topic_tags):
        base = [f"tag{i}" for i in range(500)]
        result = build_tags("among_us", base, topic_tags)
        assert len(result) <= 500

    def test_unknown_topic_uses_topic_word(self, topic_tags):
        result = build_tags("custom_topic", ["sidemen"], topic_tags)
        assert "custom topic" in result
