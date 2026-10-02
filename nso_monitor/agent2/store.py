"""The two SQLite databases each theme gets:

data/<theme>/catalogue.sqlite - WHAT EXISTS (small; read by the dashboard)
    sources   one row per monitored source (NSO x theme x connector): last check, last change, status
    items     one row per dataset / edition / table / file: title, version, status, first seen, last changed
                status: pending (new/updated, waiting to be fetched) | fetched | link_only (catalogue only,
                e.g. PDFs) | removed | error
    changes   log: when an item appeared, changed or disappeared
    runs      log: one row per daily run

data/<theme>/datasets.sqlite - THE VALUES (large; kept as a GitHub Release asset, not in git)
    observations  tidy time series from APIs: item, series, breakdown, period, value, unit
    sheet_rows    Excel / CSV / JSON files, row by row (values as a JSON list), so nothing is lost in parsing
    files         each downloaded file: address, format, size, SHA-256 fingerprint
When an item is fetched again (it changed), its old rows are replaced by the new ones.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from ..common.config import DATA_DIR

CATALOGUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY, nso TEXT, theme TEXT, connector TEXT, method TEXT,
    last_checked TEXT, last_changed TEXT, last_status TEXT, last_error TEXT, items_total INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS items (
    source_id TEXT NOT NULL, item_id TEXT NOT NULL, nso TEXT, kind TEXT, title TEXT, category TEXT,
    url TEXT, data_url TEXT, format TEXT, frequency TEXT, coverage TEXT, version TEXT,
    status TEXT NOT NULL, first_seen TEXT, last_seen TEXT, last_changed TEXT, fetched_at TEXT,
    attempts INTEGER DEFAULT 0, error TEXT, extra TEXT,
    PRIMARY KEY (source_id, item_id));
CREATE INDEX IF NOT EXISTS ix_items_status ON items (status);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, ts TEXT, source_id TEXT, nso TEXT, item_id TEXT,
    change TEXT, title TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, sources_checked INTEGER,
    sources_failed INTEGER, items_new INTEGER, items_updated INTEGER, items_removed INTEGER,
    items_fetched INTEGER, backlog INTEGER, message TEXT);
"""
DATASETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    source_id TEXT, item_id TEXT, series TEXT, breakdown TEXT, period TEXT, value REAL, value_text TEXT,
    unit TEXT, fetched_at TEXT);
CREATE INDEX IF NOT EXISTS ix_obs_item ON observations (source_id, item_id);
CREATE TABLE IF NOT EXISTS sheet_rows (
    source_id TEXT, item_id TEXT, file_url TEXT, sheet TEXT, row_no INTEGER, cells TEXT, fetched_at TEXT);
CREATE INDEX IF NOT EXISTS ix_rows_item ON sheet_rows (source_id, item_id);
CREATE TABLE IF NOT EXISTS files (
    source_id TEXT, item_id TEXT, url TEXT, format TEXT, bytes INTEGER, sha256 TEXT, fetched_at TEXT);
CREATE INDEX IF NOT EXISTS ix_files_item ON files (source_id, item_id);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def theme_dir(theme: str, root: Path | None = None) -> Path:
    path = (root or DATA_DIR) / theme
    path.mkdir(parents=True, exist_ok=True)
    return path


class CatalogueDB:
    def __init__(self, theme: str, root: Path | None = None):
        self.path = theme_dir(theme, root) / "catalogue.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(CATALOGUE_SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # -- runs -------------------------------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.db.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, **counts) -> None:
        cols = ", ".join(f"{k}=?" for k in counts)
        self.db.execute(f"UPDATE runs SET finished_at=?, {cols} WHERE run_id=?", (now(), *counts.values(), run_id))
        self.db.commit()

    # -- sources ------------------------------------------------------------------------------------
    def update_source(self, source, status: str, error: str = "", changed: bool = False) -> None:
        total = self.db.execute("SELECT COUNT(*) FROM items WHERE source_id=? AND status!='removed'", (source.id,)).fetchone()[0]
        self.db.execute(
            """INSERT INTO sources (source_id, nso, theme, connector, method, last_checked, last_changed, last_status,
                   last_error, items_total) VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (source_id) DO UPDATE SET last_checked=excluded.last_checked,
                   last_changed=COALESCE(excluded.last_changed, sources.last_changed), last_status=excluded.last_status,
                   last_error=excluded.last_error, items_total=excluded.items_total""",
            (source.id, source.nso, source.theme, source.connector, source.method, now(), now() if changed else None,
             status, error, total))
        self.db.commit()

    # -- items ---------------------------------------------------------------------------------------
    def known(self, source_id: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT * FROM items WHERE source_id=?", (source_id,))
        return {r["item_id"]: dict(r) for r in rows}

    def apply(self, run_id: int, source, changes) -> None:
        ts = now()
        c = self.db
        for it in changes.new + changes.updated:
            status = "pending" if it.fetchable else "link_only"
            c.execute(
                """INSERT INTO items (source_id, item_id, nso, kind, title, category, url, data_url, format, frequency,
                       coverage, version, status, first_seen, last_seen, last_changed, attempts, error, extra)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,?)
                   ON CONFLICT (source_id, item_id) DO UPDATE SET kind=excluded.kind, title=excluded.title,
                       category=excluded.category, url=excluded.url, data_url=excluded.data_url, format=excluded.format,
                       frequency=excluded.frequency, coverage=COALESCE(NULLIF(excluded.coverage,''), items.coverage),
                       version=excluded.version, status=excluded.status, last_seen=excluded.last_seen,
                       last_changed=excluded.last_changed, attempts=0, error=NULL, extra=excluded.extra""",
                (source.id, it.item_id, source.nso, it.kind, it.title, it.category, it.url, it.data_url, it.format,
                 it.frequency, it.coverage, it.version, status, ts, ts, ts, json.dumps(it.extra, ensure_ascii=False)))
        if changes.unchanged:
            c.executemany("UPDATE items SET last_seen=? WHERE source_id=? AND item_id=?",
                          [(ts, source.id, it.item_id) for it in changes.unchanged])
        c.executemany("UPDATE items SET status='removed', last_changed=? WHERE source_id=? AND item_id=?",
                      [(ts, source.id, item_id) for item_id in changes.removed])
        log_rows = ([(run_id, ts, source.id, source.nso, it.item_id, "new", it.title, it.version) for it in changes.new]
                    + [(run_id, ts, source.id, source.nso, it.item_id, "updated", it.title, it.version) for it in changes.updated]
                    + [(run_id, ts, source.id, source.nso, i, "removed", changes.titles.get(i, ""), "") for i in changes.removed])
        c.executemany("INSERT INTO changes (run_id, ts, source_id, nso, item_id, change, title, detail) VALUES (?,?,?,?,?,?,?,?)",
                      log_rows)
        c.commit()

    def pending(self, source_id: str, limit: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM items WHERE source_id=? AND status='pending' ORDER BY first_seen, item_id LIMIT ?",
                               (source_id, limit))
        return [dict(r) for r in rows]

    def backlog(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items WHERE status='pending'").fetchone()[0]

    def mark_fetched(self, source_id: str, item_id: str, coverage: str = "") -> None:
        self.db.execute("""UPDATE items SET status='fetched', fetched_at=?, error=NULL,
                               coverage=COALESCE(NULLIF(?, ''), coverage) WHERE source_id=? AND item_id=?""",
                        (now(), coverage, source_id, item_id))
        self.db.commit()

    def mark_error(self, source_id: str, item_id: str, error: str, max_attempts: int = 3) -> None:
        self.db.execute("""UPDATE items SET attempts=attempts+1, error=?,
                               status=CASE WHEN attempts+1 >= ? THEN 'error' ELSE 'pending' END
                           WHERE source_id=? AND item_id=?""", (error[:500], max_attempts, source_id, item_id))
        self.db.commit()


class DatasetDB:
    def __init__(self, theme: str, root: Path | None = None):
        self.path = theme_dir(theme, root) / "datasets.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.executescript(DATASETS_SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def replace(self, source_id: str, item_id: str, payload) -> None:
        """Store a fetched item, replacing whatever an earlier fetch of the same item stored."""
        ts, c = now(), self.db
        for table in ("observations", "sheet_rows", "files"):
            c.execute(f"DELETE FROM {table} WHERE source_id=? AND item_id=?", (source_id, item_id))
        c.executemany(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)",
            [(source_id, item_id, o.get("series", ""), o.get("breakdown", ""), o.get("period", ""),
              o["value"] if isinstance(o.get("value"), (int, float)) else None,
              None if isinstance(o.get("value"), (int, float)) else (None if o.get("value") is None else str(o["value"])),
              o.get("unit", ""), ts) for o in payload.observations])
        rows = []
        for sheet in payload.sheets:
            rows += [(source_id, item_id, sheet.get("file_url", ""), sheet.get("sheet", ""), n,
                      json.dumps(r, ensure_ascii=False, default=str), ts) for n, r in enumerate(sheet["rows"], 1)]
        c.executemany("INSERT INTO sheet_rows VALUES (?,?,?,?,?,?,?)", rows)
        c.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?)",
                      [(source_id, item_id, f["url"], f.get("format", ""), f.get("bytes"), f.get("sha256"), ts)
                       for f in payload.files])
        c.commit()
