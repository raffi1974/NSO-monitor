"""Step 4 - which social / economic datasets, time series and downloadable files are on the website?

Walks the site's pages (theme-related and "statistics / publications / data" links first), and records:
    * every downloadable file (PDF, XLSX, CSV, ...) - with its title, format and years mentioned
    * every page whose link text belongs to a theme (topic pages such as "Labour force", "Prices")
Each record is assigned to the theme(s) whose keywords appear in its title, surrounding text or URL.
Reading the links of one page is done by common/html.py (the same code Agent 2's scraper uses).
"""
from __future__ import annotations

import heapq
import re
from collections import Counter
from itertools import count
from urllib.parse import unquote, urlsplit

from ..common.browser import Browser
from ..common.config import Site, load_non_dataset_keywords
from ..common.html import extract_links, looks_like_spa, url_words
from ..common.keywords import ThemeMatcher, non_dataset_pattern, normalize
from ..common.text import DATA_FORMATS, content_years, coverage, extract_years, guess_frequency
from ..common.web import PoliteClient
from .site import detect_block

STAT_WORDS = re.compile(
    r"(?<![a-z])(statistic|publication|data|database|indicator|report|survey|census|release|table|yearbook|"
    r"bulletin|key figure|time series|statistique|donnee|indicateur|enquete|recensement|annuaire|tableau|rapport)"
    r"|احصاء|البيانات|المؤشرات|النشرات|الاصدارات|المسوح|التعداد|الجداول|الكتاب السنوي|المنشورات|التقارير")
MAX_RENDERS = 8


def is_supporting_document(pattern: re.Pattern | None, text: str) -> bool:
    """True if `text` (a file's or page link's own title) names a questionnaire / manual / terms of
    reference / ... (config/themes.yaml -> not_a_dataset) rather than an actual dataset."""
    return bool(pattern and pattern.search(normalize(text)))


def site_domain(url: str) -> str:
    """dosweb.dos.gov.jo -> dos.gov.jo ; www.ins.tn -> ins.tn (so databank sub-domains count as the same site)."""
    labels = (urlsplit(url).hostname or "").split(".")
    n = 3 if len(labels) >= 3 and labels[-2] in {"gov", "com", "org", "net", "edu", "ac", "co"} else 2
    return ".".join(labels[-n:])


def crawl(site: Site, site_info: dict, client: PoliteClient, browser: Browser, matcher: ThemeMatcher,
          settings: dict) -> dict:
    max_pages, max_depth = int(settings.get("max_pages", 40)), int(settings.get("max_depth", 2))
    doc_filter = non_dataset_pattern(load_non_dataset_keywords())
    start = [site_info.get("final_url") or site.url] + list(site.start_urls)
    domain = site_domain(start[0])
    order = count()
    heap = [(0, 0, next(order), u) for u in dict.fromkeys(start)]
    visited: set[str] = set()
    items: dict[str, dict] = {}
    formats: Counter = Counter()
    renders = pages = 0
    known_html = {start[0]: site_info.get("html", "")}  # the site check already fetched (or rendered) the home page

    while heap and pages < max_pages:
        depth, _, _, url = heapq.heappop(heap)
        key = url.rstrip("/")
        if key in visited:
            continue
        visited.add(key)
        html, final = known_html.pop(url, ""), url
        if not html:
            try:
                r = client.get(url, retries=1, timeout=25)
                final = str(r.url)
                html = r.text if (r.status_code < 400 and "html" in r.headers.get("content-type", "")) else ""
            except Exception:  # noqa: BLE001 - dead links are normal on large sites
                html = ""
        if (not html or looks_like_spa(html) or detect_block(html, None)) and renders < MAX_RENDERS:
            renders += 1
            page = browser.render(url)
            html, final = page.get("html") or html, page.get("final_url") or final
        if not html:
            continue
        pages += 1
        page_title, links = extract_links(html, final)

        for link in links:
            href, text = link["url"], link["text"]
            if link["kind"] == "file":
                if is_supporting_document(doc_filter, link["title"]):
                    continue  # questionnaire / manual / TOR / ... - not a dataset (config/themes.yaml)
                formats[link["format"]] += 1
                if href not in items:
                    themes = matcher.match(link["title"], link["row"], link["heading"], url_words(href))
                    years = content_years(link["title"], link["row"], unquote(href))
                    items[href] = {"kind": "file", "title": link["title"], "url": href, "page_url": final,
                                   "formats": [link["format"]], "downloads": [href], "themes": themes,
                                   "category": link["heading"] or page_title, "years": years, "coverage": coverage(years),
                                   "frequency": guess_frequency(link["title"], link["row"]),
                                   "data_file": link["format"] in DATA_FORMATS}
                continue
            if site_domain(href) != domain:
                continue
            themes = matcher.match(text, url_words(href))
            if themes and len(text) > 3 and href not in items and not is_supporting_document(doc_filter, text):
                years = extract_years(text)
                items[href] = {"kind": "page", "title": text[:200], "url": href, "page_url": final, "formats": [],
                               "downloads": [], "themes": themes, "category": page_title, "years": years,
                               "coverage": coverage(years), "frequency": guess_frequency(text), "data_file": False}
            if depth + 1 <= max_depth:
                priority = 0 if themes else (1 if STAT_WORDS.search(normalize(text + " " + url_words(href))) else 2)
                if priority < 2 or depth == 0:
                    heapq.heappush(heap, (depth + 1, priority, next(order), href))
    # A file's theme may be unknown from its own title; if nothing matched, fall back to the page it sits on.
    for it in items.values():
        if it["kind"] == "file" and not it["themes"]:
            it["themes"] = matcher.match(it["category"])
    return {"pages_crawled": pages, "pages_rendered": renders, "items": list(items.values()),
            "formats": dict(formats.most_common()), "files_total": sum(formats.values())}
