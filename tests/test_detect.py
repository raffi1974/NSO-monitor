from nso_monitor.agent2.detect import ListedItem, compare


def item(item_id, version="v1", title=None, **kw):
    return ListedItem(item_id=item_id, title=title or f"Title {item_id}", version=version, **kw)


def known(items: dict[str, dict]) -> dict[str, dict]:
    """items: {id: {"version": ..., "status": "active"}}"""
    return {k: {"item_id": k, "title": f"Title {k}", "status": v.get("status", "active"), **v} for k, v in items.items()}


def test_first_listing_everything_is_new():
    ch = compare([item("a"), item("b")], {})
    assert {i.item_id for i in ch.new} == {"a", "b"}
    assert not ch.updated and not ch.removed and not ch.suspicious


def test_unchanged_when_version_matches():
    ch = compare([item("a", version="v1")], known({"a": {"version": "v1"}}))
    assert not ch.new and not ch.updated
    assert [i.item_id for i in ch.unchanged] == ["a"]


def test_version_change_is_updated():
    ch = compare([item("a", version="v2")], known({"a": {"version": "v1"}}))
    assert [i.item_id for i in ch.updated] == ["a"]


def test_error_item_retried_only_when_its_version_changes():
    # still errored, same version -> stays put (not retried every run regardless of source)
    ch1 = compare([item("a", version="v1")], known({"a": {"version": "v1", "status": "error"}}))
    assert not ch1.updated and [i.item_id for i in ch1.unchanged] == ["a"]
    # version changed -> gets another chance
    ch2 = compare([item("a", version="v2")], known({"a": {"version": "v1", "status": "error"}}))
    assert [i.item_id for i in ch2.updated] == ["a"]


def test_missing_item_is_removed():
    # "c" and "d" keep being listed so the found-ratio (2/4 = 50%) does not trip the suspicious-run
    # guard; only "a" and "b" go missing, isolating plain removal detection from that safety net.
    prior = known({"a": {"version": "v1"}, "b": {"version": "v1"}, "c": {"version": "v1"}, "d": {"version": "v1"}})
    ch = compare([item("c"), item("d")], prior)
    assert set(ch.removed) == {"a", "b"}
    assert not ch.suspicious


def test_empty_listing_is_suspicious_not_mass_removal():
    """An empty (or near-empty) listing vs. known items is far more likely a broken/down site than
    everything really having disappeared - see the module docstring's "safety guard"."""
    ch = compare([], known({"a": {"version": "v1"}, "b": {"version": "v1"}}))
    assert ch.suspicious is True
    assert ch.removed == []


def test_removed_item_reappearing_counts_as_new():
    ch = compare([item("a")], known({"a": {"version": "v1", "status": "removed"}}))
    assert [i.item_id for i in ch.new] == ["a"]


def test_suspicious_run_marks_nothing_removed():
    prior = known({f"id{i}": {"version": "v1"} for i in range(10)})
    ch = compare([item(f"id{i}") for i in range(2)], prior, min_ratio=0.5)  # 2/10 found: well below 50%
    assert ch.suspicious is True
    assert ch.removed == []


def test_duplicate_item_id_in_listing_is_deduplicated():
    ch = compare([item("a", title="first"), item("a", title="second")], {})
    assert len(ch.new) == 1
