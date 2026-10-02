"""Small text helpers: cleaning HTML, file formats from URLs, years and frequency hidden in titles."""
from __future__ import annotations

import html
import re
from urllib.parse import unquote, urlsplit

from .keywords import normalize

FILE_FORMATS = {
    "pdf": "PDF", "xlsx": "XLSX", "xls": "XLS", "xlsm": "XLSX", "csv": "CSV", "json": "JSON", "xml": "XML",
    "zip": "ZIP", "rar": "RAR", "doc": "DOC", "docx": "DOCX", "ppt": "PPT", "pptx": "PPTX", "px": "PX",
    "sav": "SPSS", "dta": "STATA", "ods": "ODS", "txt": "TXT", "tsv": "TSV",
}
DATA_FORMATS = {"XLSX", "XLS", "CSV", "JSON", "XML", "PX", "SPSS", "STATA", "ODS", "TSV", "ZIP"}

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_YEAR = r"(?:19[5-9]\d|20[0-4]\d)"
_YEAR_RE = re.compile(rf"(?<!\d)({_YEAR})(?!\d)")
_RANGE_RE = re.compile(rf"(?<!\d)({_YEAR})\s*[-–—/]\s*({_YEAR}|\d{{2}})(?!\d)")

FREQUENCY_WORDS = [  # most specific first
    ("Monthly", ["monthly", "شهري", "شهريه", "mensuel", "mensuelle"]),
    ("Quarterly", ["quarterly", "ربع سنوي", "ربع سنويه", "trimestriel", "trimestrielle"]),
    ("Semi-annual", ["semi-annual", "semiannual", "half-year", "نصف سنوي", "semestriel"]),
    ("Annual", ["annual", "yearly", "yearbook", "سنوي", "سنويه", "annuel", "annuelle", "annuaire"]),
    ("Weekly", ["weekly", "اسبوعي", "hebdomadaire"]),
    ("Daily", ["daily", "يومي", "quotidien"]),
]


def clean_text(text: str | None, max_len: int | None = None) -> str:
    if not text:
        return ""
    t = _WS.sub(" ", html.unescape(_TAGS.sub(" ", str(text)))).strip()
    if max_len and len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].rstrip(",;:.") + "..."
    return t


def file_format(url: str | None) -> str:
    """'PDF', 'XLSX', ... when the URL points at a file, '' for ordinary pages."""
    if not url:
        return ""
    name = unquote(urlsplit(url).path).rsplit("/", 1)[-1].lower()
    return FILE_FORMATS.get(name.rsplit(".", 1)[-1], "") if "." in name else ""


def extract_years(text: str | None) -> list[int]:
    """Every plausible year in a text: 'Yearbook 2019/20 and census ٢٠١٧' -> [2017, 2019, 2020]."""
    t = normalize(text)
    years = {int(y) for y in _YEAR_RE.findall(t)}
    for start, end in _RANGE_RE.findall(t):
        s = int(start)
        e = int(end) if len(end) == 4 else (s // 100) * 100 + int(end)
        if s <= e <= s + 40:
            years.update((s, e))
    return sorted(years)


def guess_frequency(*texts: str | None) -> str:
    t = normalize(" ".join(x for x in texts if x))
    for label, words in FREQUENCY_WORDS:
        if any(normalize(w) in t for w in words):
            return label
    return ""


def period_label(year, quarter=None, month=None) -> str:
    """'2024', '2024-Q3', '2024-07' ('' when the year is unknown or 0)."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        return ""
    if year <= 0:
        return ""
    if month:
        return f"{year}-{int(month):02d}"
    if quarter:
        return f"{year}-Q{int(quarter)}"
    return str(year)


def coverage(years: list[int]) -> str:
    if not years:
        return ""
    return str(years[0]) if years[0] == years[-1] else f"{years[0]}-{years[-1]}"


def content_years(title: str, row: str, url: str) -> list[int]:
    """Years describing what a file/page actually COVERS: title/context first, the URL only as a
    fallback. A WordPress-style path like /uploads/2023/06/ names when a file was uploaded, not what
    it covers - folding it in unconditionally alongside a real content year (e.g. "...for 2007") would
    corrupt the result into a false "2007-2023" range."""
    return extract_years(f"{title} {row}") or extract_years(url)
