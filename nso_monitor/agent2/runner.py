"""Agent 2's daily run: CHECK -> COMPARE -> FETCH (only what changed) -> STORE.

1. For every enabled source, its connector lists what is published today (cheap).
2. detect.compare() finds new / updated / removed items against data/<theme>/catalogue.sqlite.
3. Only new/updated items are fetched - at most `max_fetch_per_run` in total, shared fairly between sources
   (round-robin), so one big first load (Egypt: ~7,000 series) cannot starve the others. What does not fit
   stays 'pending' and is fetched on the next runs.
4. Values go to data/<theme>/datasets.sqlite; data/run_summary.json tells the GitHub workflow whether
   anything changed (if not, nothing needs uploading).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from itertools import zip_longest

from ..common.browser import Browser
from ..common.config import DATA_DIR, browser_channel, load_agent2, load_themes
from ..common.keywords import ThemeMatcher
from ..common.web import PoliteClient
from .connectors import CONNECTORS
from .detect import compare
from .store import CatalogueDB, DatasetDB

log = logging.getLogger("nso_monitor.agent2")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run(nso: list[str] | None = None, theme: list[str] | None = None, dry_run: bool = False,
        max_fetch: int | None = None) -> dict:
    themes = load_themes()
    cfg = load_agent2(themes=themes)
    s = cfg.settings
    sources = cfg.select(nso, theme)
    summary = {"started_at": _now(), "dry_run": dry_run, "sources": [], "new": 0, "updated": 0, "removed": 0,
               "fetched": 0, "fetch_errors": 0, "backlog": 0}
    if not sources:
        log.warning("no enabled source matches - nothing to do")
        return summary
    matcher = ThemeMatcher(themes)
    holder: dict = {}

    def browser_factory() -> Browser:  # the browser only starts if a source really needs it
        if "b" not in holder:
            holder["b"] = Browser(channel=browser_channel(s), timeout_s=s["timeout_seconds"])
        return holder["b"]

    cats, datas, run_ids, connectors, counts = {}, {}, {}, {}, {}
    try:
        with PoliteClient(delay=s["request_delay_seconds"], timeout=s["timeout_seconds"],
                          concurrency=s["max_concurrency"]) as client:
            # -- 1 + 2: CHECK and COMPARE -----------------------------------------------------------
            for src in sources:
                if src.theme not in cats:
                    # --dry-run must truly write nothing: don't even create a catalogue.sqlite for a
                    # theme that doesn't have one yet (a plain "CatalogueDB(theme)" would, since opening
                    # it already runs CREATE TABLE IF NOT EXISTS). A theme with a real one is opened
                    # normally so a dry run still compares against real history.
                    has_db = (DATA_DIR / src.theme / "catalogue.sqlite").exists()
                    cats[src.theme] = CatalogueDB(src.theme) if (has_db or not dry_run) else None
                    datas[src.theme] = None if dry_run else DatasetDB(src.theme)
                    run_ids[src.theme] = 0 if dry_run else cats[src.theme].start_run()
                    counts[src.theme] = dict(sources_checked=0, sources_failed=0, items_new=0, items_updated=0,
                                             items_removed=0, items_fetched=0)
                cat, c = cats[src.theme], counts[src.theme]
                conn = CONNECTORS[src.connector](src, client, s, matcher, browser_factory)
                connectors[src.id] = conn
                c["sources_checked"] += 1
                try:
                    listed = conn.list_items()
                    if not listed:
                        raise RuntimeError("the listing came back empty")
                except Exception as exc:  # noqa: BLE001 - one broken website must not stop the others
                    c["sources_failed"] += 1
                    msg = f"{type(exc).__name__}: {str(exc)[:300]}"
                    log.warning("[%s] check FAILED - catalogue left as it was: %s", src.id, msg)
                    if not dry_run:
                        cat.update_source(src, "failed", msg)
                    summary["sources"].append({"source": src.id, "status": "failed", "error": msg})
                    continue
                known = cat.known(src.id) if cat else {}  # cat is None only for a never-before-seen dry-run theme
                ch = compare(listed, known, float(s.get("min_listing_ratio", 0.5)))
                log.info("[%s] %d listed: %d new, %d updated, %d removed%s", src.id, len(listed), len(ch.new),
                         len(ch.updated), len(ch.removed), "  (SUSPICIOUS: listing much smaller than before - "
                         "nothing marked removed)" if ch.suspicious else "")
                c["items_new"] += len(ch.new)
                c["items_updated"] += len(ch.updated)
                c["items_removed"] += len(ch.removed)
                if not dry_run:
                    cat.apply(run_ids[src.theme], src, ch)
                    cat.update_source(src, "suspicious" if ch.suspicious else "ok", changed=ch.any)
                summary["sources"].append({"source": src.id, "status": "suspicious" if ch.suspicious else "ok",
                                           "listed": len(listed), "new": len(ch.new), "updated": len(ch.updated),
                                           "removed": len(ch.removed),
                                           "to_fetch": sum(1 for i in ch.new + ch.updated if i.fetchable)})

            # -- 3 + 4: FETCH only what changed (plus earlier backlog), fairly shared ---------------
            if not dry_run:
                budget = int(max_fetch if max_fetch is not None else s["max_fetch_per_run"])
                queues = [cats[src.theme].pending(src.id, budget) for src in sources if src.id in connectors]
                order = [item for group in zip_longest(*queues) for item in group if item]
                for item in order[:budget]:
                    src_theme = connectors[item["source_id"]].source.theme
                    cat, data = cats[src_theme], datas[src_theme]
                    try:
                        payload = connectors[item["source_id"]].fetch(item)
                        data.replace(item["source_id"], item["item_id"], payload)
                        cat.mark_fetched(item["source_id"], item["item_id"], payload.coverage)
                        counts[src_theme]["items_fetched"] += 1
                    except Exception as exc:  # noqa: BLE001 - retried up to 3 times over the next runs
                        cat.mark_error(item["source_id"], item["item_id"], f"{type(exc).__name__}: {exc}")
                        summary["fetch_errors"] += 1
                        log.debug("fetch failed %s: %s", item["item_id"], exc)
                for tid, cat in cats.items():
                    backlog = cat.backlog()
                    summary["backlog"] += backlog
                    cat.finish_run(run_ids[tid], **counts[tid], backlog=backlog,
                                   message=f"{counts[tid]['items_fetched']} fetched, {backlog} waiting")
    finally:
        if "b" in holder:
            holder["b"].close()
        for tid, cat in cats.items():
            if cat:
                cat.close()
            if datas.get(tid):
                datas[tid].close()

    for c in counts.values():
        summary["new"] += c["items_new"]
        summary["updated"] += c["items_updated"]
        summary["removed"] += c["items_removed"]
        summary["fetched"] += c["items_fetched"]
    summary["data_changed"] = bool(summary["new"] or summary["updated"] or summary["removed"] or summary["fetched"])
    summary["finished_at"] = _now()
    if not dry_run:
        (DATA_DIR / "run_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
