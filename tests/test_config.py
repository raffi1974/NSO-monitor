"""Loads the real config/ directory: doubles as a check that the shipped registry (22 sites, both
themes, the CAPMAS sources) stays valid as it's edited."""
from nso_monitor.common.config import ConfigError, load_agent1, load_agent2, load_non_dataset_keywords, load_themes


def test_shipped_themes_are_valid():
    themes = load_themes()
    assert {"social_statistics", "economic_statistics"} <= set(themes)
    for t in themes.values():
        assert any(t.keywords.values())


def test_not_a_dataset_block_is_not_loaded_as_a_theme():
    assert "not_a_dataset" not in load_themes()


def test_shipped_non_dataset_keywords_catch_the_real_lebanon_example():
    from nso_monitor.common.keywords import non_dataset_pattern, normalize
    pat = non_dataset_pattern(load_non_dataset_keywords())
    assert pat.search(normalize("Anthropometry questionnaire (Arabic)"))
    assert not pat.search(normalize("Labour Force Survey 2023"))


def test_shipped_sites_are_valid_and_include_comoros():
    cfg = load_agent1()
    ids = {s.id for s in cfg.sites}
    assert len(cfg.sites) == 22
    assert "COM" in ids and "EGY" in ids
    assert len(ids) == len(cfg.sites)  # no duplicate ids


def test_select_sites_by_id():
    cfg = load_agent1()
    assert [s.id for s in cfg.select(["jor", "tun"])] == ["JOR", "TUN"]
    try:
        cfg.select(["ZZZ"])
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "ZZZ" in str(exc)


def test_shipped_agent2_sources_are_valid():
    cfg = load_agent2()
    assert any(s.nso == "EGY" and s.connector == "capmas" for s in cfg.sources)
    assert all(s.id for s in cfg.sources)


def test_agent2_select_includes_explicit_nso_even_if_disabled(tmp_path):
    _write(tmp_path, "sources:\n  - {nso: ZZZ, theme: social_statistics, connector: scrape, enabled: false, "
                     "pages: ['https://example.org']}\n")
    cfg = load_agent2(tmp_path / "agent2_sources.yaml", themes=load_themes())
    assert cfg.select() == []
    assert [s.nso for s in cfg.select(nso=["ZZZ"])] == ["ZZZ"]


def test_agent2_rejects_unknown_theme_and_connector(tmp_path):
    _write(tmp_path, "sources:\n  - {nso: ZZZ, theme: not_a_theme, connector: not_a_connector}\n")
    try:
        load_agent2(tmp_path / "agent2_sources.yaml", themes=load_themes())
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "not_a_theme" in str(exc) and "not_a_connector" in str(exc)


def test_agent2_scrape_connector_needs_pages(tmp_path):
    _write(tmp_path, "sources:\n  - {nso: ZZZ, theme: social_statistics, connector: scrape}\n")
    try:
        load_agent2(tmp_path / "agent2_sources.yaml", themes=load_themes())
        assert False, "expected ConfigError"
    except ConfigError as exc:
        assert "pages" in str(exc)


def _write(tmp_path, sources_yaml: str) -> None:
    (tmp_path / "agent2_sources.yaml").write_text(sources_yaml, encoding="utf-8")
