"""The contract every connector follows, and the file helpers they share.

A connector knows how to talk to ONE kind of website. It must do exactly two things:
    list_items()  -> list[ListedItem]   cheap: what is published right now, each with a version stamp
    fetch(item)   -> Payload            only called for new/updated items: download and parse the data
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from dataclasses import dataclass, field

from ...common.config import Source
from ...common.keywords import ThemeMatcher
from ...common.text import file_format
from ...common.web import PoliteClient
from ..detect import ListedItem  # noqa: F401  (re-exported for connectors)

CONNECTORS: dict[str, type["Connector"]] = {}
MAX_ROWS_PER_SHEET = 50_000


def register(cls):
    CONNECTORS[cls.name] = cls
    return cls


@dataclass
class Payload:
    observations: list[dict] = field(default_factory=list)   # {series, breakdown, period, value, unit}
    sheets: list[dict] = field(default_factory=list)         # {file_url, sheet, rows: [[cell, ...], ...]}
    files: list[dict] = field(default_factory=list)          # {url, format, bytes, sha256}
    coverage: str = ""                                       # e.g. "2010-2024", from the observations


class Connector:
    name = ""
    method = "api"      # api | scrape

    def __init__(self, source: Source, client: PoliteClient, settings: dict, matcher: ThemeMatcher, browser_factory):
        self.source, self.client, self.settings, self.matcher = source, client, settings, matcher
        self.options = dict(source.options or {})
        self._browser_factory = browser_factory
        self.formats = {f.upper() for f in settings.get("download_formats", [])}

    def list_items(self) -> list[ListedItem]:
        raise NotImplementedError

    def fetch(self, item: dict) -> Payload:
        raise NotImplementedError

    # -- helpers --------------------------------------------------------------------------------
    @property
    def browser(self):
        return self._browser_factory()

    def in_theme(self, *texts: str) -> bool:
        return self.source.theme in self.matcher.match(*texts)

    def wants(self, fmt: str) -> bool:
        return (fmt or "").upper() in self.formats or (fmt.upper() == "XLS" and "XLSX" in self.formats)

    def download(self, url: str, fmt: str = "", headers: dict | None = None) -> tuple[bytes, dict]:
        r = self.client.get(url, check_robots=False, headers=headers or {})
        r.raise_for_status()
        limit = float(self.settings.get("max_file_mb", 25)) * 1024 * 1024
        if len(r.content) > limit:
            raise ValueError(f"file larger than max_file_mb ({len(r.content) / 1e6:.1f} MB)")
        return r.content, {"url": url, "format": fmt or file_format(url), "bytes": len(r.content),
                           "sha256": hashlib.sha256(r.content).hexdigest()}

    def file_payload(self, url: str, fmt: str = "") -> Payload:
        content, meta = self.download(url, fmt)
        return Payload(sheets=parse_table_file(content, meta["format"], url), files=[meta])


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v.strip() if isinstance(v, str) else v


def _trim(rows):
    out = []
    for row in rows:
        row = [_cell(v) for v in row]
        while row and row[-1] == "":
            row.pop()
        if row:
            out.append(row)
        if len(out) >= MAX_ROWS_PER_SHEET:
            break
    return out


def parse_table_file(content: bytes, fmt: str, url: str) -> list[dict]:
    """Excel / CSV / JSON -> [{file_url, sheet, rows}] keeping every non-empty row (no guessing of layouts)."""
    fmt = (fmt or "").upper()
    sheets = []
    if fmt in ("XLSX", "XLSM"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        for ws in wb.worksheets:
            sheets.append({"file_url": url, "sheet": ws.title, "rows": _trim(ws.iter_rows(values_only=True))})
        wb.close()
    elif fmt == "XLS":
        import xlrd

        book = xlrd.open_workbook(file_contents=content)
        for sh in book.sheets():
            sheets.append({"file_url": url, "sheet": sh.name, "rows": _trim(sh.row_values(i) for i in range(sh.nrows))})
    elif fmt in ("CSV", "TSV"):
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if fmt == "TSV" else csv.excel
        sheets.append({"file_url": url, "sheet": "", "rows": _trim(csv.reader(io.StringIO(text), dialect))})
    elif fmt == "JSON":
        data = json.loads(content.decode("utf-8-sig"))
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        if data and isinstance(data[0], dict):
            header = list(dict.fromkeys(k for d in data for k in d))
            rows = [header] + [[json.dumps(d.get(k), ensure_ascii=False) if isinstance(d.get(k), (dict, list)) else d.get(k)
                                for k in header] for d in data]
        else:
            rows = [[json.dumps(x, ensure_ascii=False)] for x in data]
        sheets.append({"file_url": url, "sheet": "", "rows": _trim(rows)})
    return sheets


def coverage_of(periods: list[str]) -> str:
    ps = sorted(p for p in periods if p)
    return "" if not ps else (ps[0] if ps[0] == ps[-1] else f"{ps[0]}-{ps[-1]}")
