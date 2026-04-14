from update_db import assign_topic


TOPICS_CONFIG = {
    "among_us": ["among us"],
    "try_not_to_laugh": ["try not to laugh", "tntl", "funniest moments"],
    "mukbang": ["mukbang", "eating challenge"],
    "football": ["football", "penalty", "sidemen fc"],
    "quiz": ["quiz", "trivia", "jeopardy"],
    "gaming": ["gta", "fifa", "fortnite", "minecraft"],
    "general": [],
}


class TestAssignTopic:

    def test_exact_match(self):
        assert assign_topic("SIDEMEN AMONG US", TOPICS_CONFIG) == "among_us"

    def test_case_insensitive(self):
        assert assign_topic("sidemen among us game", TOPICS_CONFIG) == "among_us"

    def test_partial_match(self):
        assert assign_topic("SIDEMEN TRY NOT TO LAUGH #5", TOPICS_CONFIG) == "try_not_to_laugh"

    def test_keyword_match(self):
        assert assign_topic("SIDEMEN TNTL CHALLENGE", TOPICS_CONFIG) == "try_not_to_laugh"

    def test_falls_to_general(self):
        assert assign_topic("SIDEMEN DO SOMETHING RANDOM", TOPICS_CONFIG) == "general"

    def test_none_title(self):
        assert assign_topic(None, TOPICS_CONFIG) == "general"

    def test_empty_title(self):
        assert assign_topic("", TOPICS_CONFIG) == "general"

    def test_first_match_wins(self):
        # "SIDEMEN AMONG US QUIZ" should match among_us first (it's ordered before quiz)
        result = assign_topic("SIDEMEN AMONG US QUIZ", TOPICS_CONFIG)
        assert result == "among_us"

    def test_gaming_keywords(self):
        assert assign_topic("SIDEMEN PLAY GTA V", TOPICS_CONFIG) == "gaming"
        assert assign_topic("SIDEMEN FIFA 24 TOURNAMENT", TOPICS_CONFIG) == "gaming"

    def test_football_keywords(self):
        assert assign_topic("SIDEMEN FC VS YOUTUBE ALLSTARS", TOPICS_CONFIG) == "football"
        assert assign_topic("SIDEMEN PENALTY SHOOTOUT", TOPICS_CONFIG) == "football"
