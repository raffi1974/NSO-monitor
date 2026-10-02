"""Aggregates output/agent1/*.json and every data/<theme>/catalogue.sqlite into dashboard/data.js,
then copies the static HTML/CSS/JS shell next to it.

Design, same reasoning as before: fully offline (no server, no CDN - data.js is loaded with
<script src>, not fetch(), because browsers block fetch() of local file:// pages), and only a capped,
alphabetically sorted SAMPLE of each theme's items is embedded so the page stays fast as more NSOs are
onboarded - the complete list always lives in the SQLite databases themselves.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

from ..common.config import DASHBOARD_DIR, DATA_DIR, OUTPUT_DIR, load_agent1, load_themes

TEMPLATE_DIR = Path(__file__).parent / "template"
SAMPLE_LIMIT = 400
CHANGES_WINDOW_DAYS = 30
STATUS_ORDER = {"failed": 0, "suspicious": 1, "diagnosed_only": 2, "not_diagnosed": 3, "ok": 4}


def _load_agent1_results() -> dict[str, dict]:
    out = {}
    for p in OUTPUT_DIR.glob("agent1/*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out[d["id"]] = d
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def _theme_from_catalogue(db: sqlite3.Connection, nso: str, theme: str, since: str) -> dict | None:
    rows = db.execute("SELECT * FROM sources WHERE nso=? AND theme=?", (nso, theme)).fetchall()
    if not rows:
        return None
    src_ids = [r["source_id"] for r in rows]
    q = f"SELECT * FROM items WHERE source_id IN ({','.join('?' * len(src_ids))}) AND status!='removed'"
    items = [dict(r) for r in db.execute(q, src_ids)]
    by_kind = Counter(i["kind"] for i in items)
    by_category = Counter(i["category"] or "Uncategorised" for i in items)
    by_status = Counter(i["status"] for i in items)
    cq = (f"SELECT change, COUNT(*) n FROM changes WHERE source_id IN ({','.join('?' * len(src_ids))}) "
         "AND ts>=? GROUP BY change")
    changes = {r["change"]: r["n"] for r in db.execute(cq, [*src_ids, since])}
    ordered = sorted(items, key=lambda i: (i["category"] or "", i["title"] or ""))
    return {
        "status": "suspicious" if any(r["last_status"] == "suspicious" for r in rows) else "ok",
        "last_checked": max((r["last_checked"] or "" for r in rows), default=""),
        "last_changed": max((r["last_changed"] or "" for r in rows), default=""),
        "n_items": len(items), "n_fetched": by_status.get("fetched", 0), "n_pending": by_status.get("pending", 0),
        "by_kind": dict(by_kind), "by_category": dict(by_category.most_common(12)),
        "changes": {"new": changes.get("new", 0), "updated": changes.get("updated", 0),
                   "removed": changes.get("removed", 0), "window_days": CHANGES_WINDOW_DAYS},
        "sample_total": len(ordered), "sample_shown": min(len(ordered), SAMPLE_LIMIT),
        "sample": [{"title": i["title"], "category": i["category"], "kind": i["kind"], "format": i["format"],
                    "frequency": i["frequency"], "coverage": i["coverage"], "status": i["status"], "url": i["url"]}
                  for i in ordered[:SAMPLE_LIMIT]],
    }


def _theme_from_agent1(diag: dict, theme_id: str) -> dict | None:
    th = (diag.get("themes") or {}).get(theme_id)
    if not th or not th.get("items"):
        return None
    items = th["items"]
    by_kind = Counter(i["kind"] for i in items)
    by_category = Counter(i.get("category") or "Uncategorised" for i in items)
    ordered = sorted(items, key=lambda i: (i.get("category") or "", i.get("title") or ""))
    return {
        "status": "diagnosed_only", "last_checked": diag.get("checked_at", ""), "last_changed": "",
        "n_items": len(items), "n_fetched": 0, "n_pending": 0, "by_kind": dict(by_kind),
        "by_category": dict(by_category.most_common(12)), "changes": {"new": 0, "updated": 0, "removed": 0,
                                                                       "window_days": CHANGES_WINDOW_DAYS},
        "sample_total": len(ordered), "sample_shown": min(len(ordered), SAMPLE_LIMIT),
        "sample": [{"title": i.get("title"), "category": i.get("category"), "kind": i["kind"],
                    "format": (i.get("formats") or [""])[0], "frequency": i.get("frequency", ""),
                    "coverage": i.get("coverage", ""), "status": "not_monitored",
                    "url": (i.get("downloads") or [i.get("url", "")])[0]} for i in ordered[:SAMPLE_LIMIT]],
    }


def build_data(only: list[str] | None = None) -> dict:
    themes = load_themes()
    sites = [s for s in load_agent1().sites if not only or s.id in only]
    agent1 = _load_agent1_results()
    since = (dt.date.today() - dt.timedelta(days=CHANGES_WINDOW_DAYS)).isoformat()
    dbs = {tid: sqlite3.connect(p) for tid in themes if (p := DATA_DIR / tid / "catalogue.sqlite").exists()}
    for db in dbs.values():
        db.row_factory = sqlite3.Row

    monitored_themes = [tid for tid in themes if tid in dbs]
    nsos, feed = [], []
    for site in sites:
        diag = agent1.get(site.id)
        theme_data = {}
        for tid in themes:
            live = _theme_from_catalogue(dbs[tid], site.id, tid, since) if tid in dbs else None
            theme_data[tid] = live or (_theme_from_agent1(diag, tid) if diag else None)
        active = {tid: t for tid, t in theme_data.items() if t}
        if active:
            status = min((t["status"] for t in active.values()), key=lambda s: STATUS_ORDER.get(s, 9))
        else:
            status = "not_diagnosed"
        api = (diag or {}).get("api", {})
        nsos.append({
            "id": site.id, "country": site.country, "nso": site.nso, "url": site.url,
            "languages": site.languages, "english": site.english, "status": status,
            "diagnosed_at": (diag or {}).get("checked_at", ""),
            "api": {"status": api.get("status", "not checked"), "python_access": api.get("python_access", "unknown"),
                   "summary": api.get("summary", ""), "docs": api.get("docs", []), "how_to": api.get("how_to", {})},
            "themes": {tid: t for tid, t in theme_data.items() if t},
            "report_path": f"output/agent1/{site.id}.txt" if diag else "",
        })
        for tid in monitored_themes:
            src_ids = [r["source_id"] for r in dbs[tid].execute("SELECT source_id FROM sources WHERE nso=?", (site.id,))]
            if not src_ids:
                continue
            q = (f"SELECT * FROM changes WHERE source_id IN ({','.join('?' * len(src_ids))}) AND ts>=? "
                "ORDER BY ts DESC LIMIT 100")
            for row in dbs[tid].execute(q, [*src_ids, since]):
                feed.append({"nso": site.id, "country": site.country, "theme": tid, "ts": row["ts"],
                            "change": row["change"], "title": row["title"], "detail": row["detail"]})
    for db in dbs.values():
        db.close()
    feed.sort(key=lambda c: c["ts"], reverse=True)
    nsos.sort(key=lambda n: (STATUS_ORDER.get(n["status"], 9), n["country"]))

    return {
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "themes": {tid: t.label for tid, t in themes.items()},
        "summary": {
            "total_nsos": len(nsos), "diagnosed": sum(1 for n in nsos if n["diagnosed_at"]),
            "monitored": sum(1 for n in nsos if any(t["status"] in ("ok", "suspicious") for t in n["themes"].values())),
            "with_api": sum(1 for n in nsos if n["api"]["status"] not in ("none", "not checked", "unknown")),
            "total_items": sum(t["n_items"] for n in nsos for t in n["themes"].values()),
            "changes_window_days": CHANGES_WINDOW_DAYS, "changes_recent": len(feed),
        },
        "nsos": nsos, "changes_feed": feed[:300],
    }


def build_dashboard(only: list[str] | None = None) -> Path:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    for asset in ("index.html", "style.css", "app.js"):
        src = TEMPLATE_DIR / asset
        if src.exists():
            shutil.copy2(src, DASHBOARD_DIR / asset)
    data = build_data(only)
    (DASHBOARD_DIR / "data.js").write_text(
        "// Generated by `python build_dashboard.py`. Do not edit by hand.\n"
        # window.DASHBOARD_DATA = ..., NOT `const DASHBOARD_DATA = ...`: a top-level const/let in a
        # classic script does NOT become a window property (unlike var or a plain assignment) - app.js
        # reads window.DASHBOARD_DATA, which a const declaration here would leave undefined forever,
        # with no console error to point at why (this was found by an actual rendered screenshot check,
        # not by the build script "succeeding" - a Python-side smoke test would never have caught it).
        "window.DASHBOARD_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
    return DASHBOARD_DIR / "index.html"
