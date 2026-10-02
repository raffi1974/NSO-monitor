"""Reading a web page's links - shared by Agent 1 (crawl.py) and Agent 2 (scrape connector), so both agents
see a page exactly the same way.

extract_links(html, base_url) returns every useful link with:
    kind     "file" (PDF, XLSX, CSV...) or "page"
    url      absolute address
    title    the best human title: the link text, or - when the link just says "Download" / "PDF" /
             "تحميل" - the text of its table row or list item, or the nearest heading
    heading  nearest heading above the link;  row: text of its table row / list item
    format   "PDF", "XLSX", ... ("" for pages)
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .keywords import normalize
from .text import clean_text, file_format

SPA_MARKERS = ('id="root"', "id='root'", "<app-root", 'id="__next"', 'id="app"', "enable javascript", "ng-version")
GENERIC_LABELS = {"download", "telecharger", "pdf", "xlsx", "xls", "csv", "doc", "docx", "zip", "click", "here",
                  "more", "view", "open", "file", "link", "details", "read", "تحميل", "تنزيل", "ملف", "عرض",
                  "التفاصيل", "هنا", "اضغط", "المزيد", "plus", "voir", "lire", "fichier"}
SKIP = re.compile(r"/(login|logout|signin|register|search|print|share|rss|feed)\b|\.(jpe?g|png|gif|svg|webp|mp4|mp3|css|js)$"
                  r"|facebook|twitter|linkedin|whatsapp|youtube|instagram", re.I)


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def looks_like_spa(html: str) -> bool:
    """True for JavaScript-only pages (almost no text + an app container): they need a real browser."""
    low = (html or "").lower()
    return len(visible_text(html)) < 400 and any(m in low for m in SPA_MARKERS)


def is_generic_label(text: str) -> bool:
    words = normalize(text).replace(".", " ").split()
    return not words or all(w.strip(":,-()") in GENERIC_LABELS for w in words)


def url_words(url: str) -> str:
    """'/stats/labour-force_2023.xlsx' -> ' stats labour force 2023 xlsx' (lets keywords match file names)."""
    return re.sub(r"[-_/.%+=?&]+", " ", unquote(urlsplit(url).path))


def _context(a) -> tuple[str, str]:
    heading = a.find_previous(["h1", "h2", "h3", "h4", "h5"])
    holder = a.find_parent(["tr", "li", "p"]) or a.find_parent("div")
    row = clean_text(holder.get_text(" "), 300) if holder else ""
    own = clean_text(a.get_text(" "))
    if own and own in row:
        row = row.replace(own, " ").strip(" -|:")
    return (clean_text(heading.get_text(" "), 150) if heading else ""), clean_text(row, 250)


def extract_links(html: str, base_url: str) -> tuple[str, list[dict]]:
    """(page title, links) for one HTML page."""
    soup = BeautifulSoup(html or "", "html.parser")
    page_title = clean_text(soup.title.get_text(" ") if soup.title else "", 150)
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        raw = a["href"].strip()
        if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = urljoin(base_url, raw).split("#")[0]
        if not url.startswith(("http://", "https://")) or SKIP.search(url) or url in seen:
            continue
        seen.add(url)
        text = clean_text(a.get_text(" ")) or clean_text(a.get("title", ""))
        fmt = file_format(url)
        if fmt:
            heading, row = _context(a)
            title = text if (len(text) > 3 and not is_generic_label(text)) else (row or heading or unquote(url.rsplit("/", 1)[-1]))
            out.append({"kind": "file", "url": url, "text": text, "title": title[:200], "heading": heading, "row": row,
                        "format": fmt})
        else:
            out.append({"kind": "page", "url": url, "text": text, "title": text[:200], "heading": "", "row": "",
                        "format": ""})
    return page_title, out
