from src.youtube_upload import format_title, format_description, build_tags


class TestFormatTitle:

    def test_basic_title(self):
        result = format_title("among_us", 7200)
        assert result == "SIDEMEN AMONG US - 2 HOUR SPECIAL"

    def test_rounds_hours(self):
        result = format_title("mukbang", 5400)  # 1.5 hours
        assert result == "SIDEMEN MUKBANG - 2 HOUR SPECIAL"

    def test_minimum_one_hour(self):
        result = format_title("quiz", 600)  # 10 minutes
        assert result == "SIDEMEN QUIZ - 1 HOUR SPECIAL"

    def test_large_duration(self):
        result = format_title("try_not_to_laugh", 43200)  # 12 hours
        assert result == "SIDEMEN TRY NOT TO LAUGH - 12 HOUR SPECIAL"

    def test_unknown_topic_fallback(self):
        result = format_title("unknown_topic", 3600)
        assert "UNKNOWN TOPIC" in result

    def test_general_topic(self):
        result = format_title("general", 3600)
        assert result == "SIDEMEN COMPILATION - 1 HOUR SPECIAL"


class TestFormatDescription:

    def test_template_substitution(self):
        template = "Best {topic} moments! #{topic_tag}"
        result = format_description("among_us", template)
        assert "AMONG US" in result
        assert "#amongus" in result

    def test_empty_template(self):
        result = format_description("quiz", "")
        assert result == ""


class TestBuildTags:

    def test_combines_base_and_topic_tags(self):
        base = ["sidemen", "compilation"]
        result = build_tags("among_us", base)
        assert "sidemen" in result
        assert "compilation" in result
        assert "among us" in result

    def test_no_duplicates(self):
        base = ["sidemen", "among us"]
        result = build_tags("among_us", base)
        assert result.count("among us") == 1

    def test_max_500_tags(self):
        base = [f"tag{i}" for i in range(500)]
        result = build_tags("among_us", base)
        assert len(result) <= 500

    def test_unknown_topic_uses_topic_word(self):
        result = build_tags("custom_topic", ["sidemen"])
        assert "custom topic" in result
