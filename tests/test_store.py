from types import SimpleNamespace

from nso_monitor.agent2.detect import ListedItem, compare
from nso_monitor.agent2.store import CatalogueDB, DatasetDB


def source(nso="X", theme="t", connector="scrape"):
    return SimpleNamespace(id=f"{nso}_{theme}_{connector}", nso=nso, theme=theme, connector=connector, method="scrape")


def run_once(cat, src, listed):
    run_id = cat.start_run()
    ch = compare(listed, cat.known(src.id))
    cat.apply(run_id, src, ch)
    cat.update_source(src, "suspicious" if ch.suspicious else "ok", changed=ch.any)
    cat.finish_run(run_id, sources_checked=1, sources_failed=0, items_new=len(ch.new), items_updated=len(ch.updated),
                   items_removed=len(ch.removed), items_fetched=0, backlog=cat.backlog())
    return ch


def test_baseline_then_update_then_removal(tmp_path):
    cat = CatalogueDB("t", root=tmp_path)
    src = source()
    try:
        run_once(cat, src, [ListedItem("a", "Item A", version="v1"), ListedItem("b", "Item B", version="v1")])
        assert {r["item_id"] for r in cat.known(src.id).values()} == {"a", "b"}
        assert cat.db.execute("SELECT COUNT(*) FROM changes WHERE change='new'").fetchone()[0] == 2

        # "c" keeps being listed so the found-ratio stays above the suspicious-run threshold; only "b"
        # goes missing, isolating removal detection from that safety net (see test_detect.py for it).
        run_once(cat, src, [ListedItem("a", "Item A", version="v1"), ListedItem("c", "Item C", version="v1")])
        run_once(cat, src, [ListedItem("a", "Item A", version="v2"), ListedItem("c", "Item C", version="v1")])
        known = cat.known(src.id)
        assert known["a"]["status"] == "pending" and known["a"]["version"] == "v2"
        assert known["b"]["status"] == "removed"
        assert cat.db.execute("SELECT 1 FROM changes WHERE item_id='b' AND change='removed'").fetchone() is not None
    finally:
        cat.close()


def test_fetch_status_transitions(tmp_path):
    cat = CatalogueDB("t", root=tmp_path)
    src = source()
    try:
        run_once(cat, src, [ListedItem("a", "Item A", version="v1", fetchable=True)])
        assert cat.known(src.id)["a"]["status"] == "pending"
        assert cat.backlog() == 1

        cat.mark_fetched(src.id, "a", coverage="2020-2024")
        row = cat.known(src.id)["a"]
        assert row["status"] == "fetched" and row["coverage"] == "2020-2024"
        assert cat.backlog() == 0

        # unfetchable items (e.g. PDF-only editions) are never queued as backlog
        run_once(cat, src, [ListedItem("a", "Item A", version="v1"), ListedItem("b", "Item B (PDF)", version="v1", fetchable=False)])
        assert cat.known(src.id)["b"]["status"] == "link_only"
        assert cat.backlog() == 0
    finally:
        cat.close()


def test_mark_error_gives_up_after_max_attempts(tmp_path):
    cat = CatalogueDB("t", root=tmp_path)
    src = source()
    try:
        run_once(cat, src, [ListedItem("a", "Item A", version="v1")])
        for _ in range(3):
            cat.mark_error(src.id, "a", "boom", max_attempts=3)
        assert cat.known(src.id)["a"]["status"] == "error"
    finally:
        cat.close()


def test_dataset_db_replace_overwrites_previous_fetch(tmp_path):
    ds = DatasetDB("t", root=tmp_path)
    try:
        payload1 = SimpleNamespace(observations=[{"series": "s", "breakdown": "", "period": "2020", "value": 1, "unit": ""}],
                                   sheets=[], files=[])
        ds.replace("src", "a", payload1)
        assert ds.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1

        payload2 = SimpleNamespace(observations=[{"series": "s", "breakdown": "", "period": "2021", "value": 2, "unit": ""}],
                                   sheets=[], files=[])
        ds.replace("src", "a", payload2)
        rows = ds.db.execute("SELECT period, value FROM observations").fetchall()
        assert rows == [("2021", 2.0)]  # old rows gone, not accumulated
    finally:
        ds.close()
