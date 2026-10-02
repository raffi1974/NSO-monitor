from nso_monitor.common.config import Theme
from nso_monitor.common.keywords import ThemeMatcher, non_dataset_pattern, normalize


def test_normalize_folds_arabic_accents_and_digits():
    assert normalize("الإحصاءات الحيوية ٢٠٢٤") == "الاحصاءات الحيويه 2024"
    assert normalize("Éducation") == "education"
    assert normalize("  multiple   spaces ") == "multiple spaces"
    assert normalize(None) == ""


def _themes():
    return {
        "social": Theme(id="social", label="Social", keywords={
            "en": ["health", "hospital"], "ar": ["الصحه"]}),
        "economic": Theme(id="economic", label="Economic", keywords={
            "en": ["price", "inflation"]}, exclude={"en": ["price of admission"]}),
    }


def test_latin_matches_at_word_start_only():
    m = ThemeMatcher(_themes())
    assert m.match("Household Health Survey") == ["social"]
    assert m.match("Healthcare bulletin") == ["social"]          # prefix match ("stemming"), by design
    assert m.match("The unhealthy debate") == []                 # mid-word: must not match


def test_arabic_matches_with_attached_prefixes():
    m = ThemeMatcher(_themes())
    assert m.match("وزارة الصحه") == ["social"]  # وزارة الصحه


def test_multiple_themes_and_exclusion():
    m = ThemeMatcher(_themes())
    assert set(m.match("Health price index bulletin")) == {"social", "economic"}
    assert m.match("Price of admission to the museum") == []     # excluded despite matching "price"


def test_no_match():
    m = ThemeMatcher(_themes())
    assert m.match("Foreign trade statistics") == []


def test_matched_words_explains_a_hit():
    m = ThemeMatcher(_themes())
    assert m.matched_words("social", "Household Health Survey") == ["health"]


def test_non_dataset_pattern_catches_supporting_documents_not_real_datasets():
    pat = non_dataset_pattern({"en": ["questionnaire", "terms of reference"], "ar": ["استبيان"]})
    assert pat.search(normalize("Anthropometry Questionnaire (Arabic)"))
    assert pat.search(normalize("Terms of Reference for the 2024 Census"))
    assert pat.search(normalize("استبيان الاسره"))
    assert not pat.search(normalize("Labour Force Survey 2023"))


def test_non_dataset_pattern_empty_keywords_matches_nothing():
    assert non_dataset_pattern({}) is None
