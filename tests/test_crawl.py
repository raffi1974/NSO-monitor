from nso_monitor.agent1.crawl import is_supporting_document
from nso_monitor.common.keywords import non_dataset_pattern


def test_is_supporting_document_flags_questionnaires_and_manuals():
    pat = non_dataset_pattern({"en": ["questionnaire", "field manual"]})
    assert is_supporting_document(pat, "Anthropometry questionnaire (Arabic)")
    assert is_supporting_document(pat, "Enumerator's Field Manual 2024")
    assert not is_supporting_document(pat, "Consumer Price Index bulletin 2024")


def test_is_supporting_document_with_no_pattern_never_flags_anything():
    assert is_supporting_document(None, "Anthropometry questionnaire (Arabic)") is False
