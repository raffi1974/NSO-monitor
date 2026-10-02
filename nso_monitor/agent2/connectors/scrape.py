"""Any website without an API: read the configured pages and track the files linked from them.

config/agent2_sources.yaml:
    - nso: JOR
      theme: social_statistics
      connector: scrape
      pages: [https://dosweb.dos.gov.jo/..., ...]   # pages that list the theme's files (from Agent 1's report)
      render_js: auto        # auto | true | false  - true = always open the pages in a real browser
      options:
        match_theme: true    # keep only files whose title/context matches the theme's keywords
                             # (set false when the pages are dedicated to the theme)
        head_check: true     # ask the server if a known data file changed (ETag / Last-Modified / size)

Items: one per file link ("file:<hash of its address>"); version = the server's ETag / Last-Modified / size
for data files (so a file replaced at the same address counts as updated). PDFs are catalogued as links only.
"""
from __future__ import annotations

import hashlib
from urllib.parse import unquote

from ...common.html import extract_links, looks_like_spa, url_words
from ...common.text import content_years, coverage, guess_frequency
from .base import Connector, ListedItem, Payload, register


@register
class ScrapeConnector(Connector):
    name = "scrape"
    method = "scrape"

    def _html(self, url: str) -> tuple[str, str]:
        mode = str(self.source.render_js).lower()
        if mode == "true":
            page = self.browser.render(url)
            return page.get("html") or "", page.get("final_url") or url
        r = self.client.get(url)
        html = r.text if (r.status_code < 400 and "html" in r.headers.get("content-type", "")) else ""
        if mode == "auto" and (not html or looks_like_spa(html)):
            page = self.browser.render(url)
            return page.get("html") or html, page.get("final_url") or str(r.url)
        return html, str(r.url)

    def list_items(self) -> list[ListedItem]:
        match_theme = self.options.get("match_theme", True)
        head_check = self.options.get("head_check", True)
        items, seen = [], set()
        for page in self.source.pages:
            html, final = self._html(page)
            if not html:
                raise RuntimeError(f"no HTML from {page}")  # the runner then keeps yesterday's catalogue untouched
            page_title, links = extract_links(html, final)
            for link in links:
                if link["kind"] != "file" or link["url"] in seen:
                    continue
                if match_theme and not self.in_theme(link["title"], link["row"], link["heading"], url_words(link["url"])):
                    continue
                seen.add(link["url"])
                version = ""
                if head_check and self.wants(link["format"]):
                    try:
                        h = self.client.head(link["url"], check_robots=False)
                        version = h.headers.get("etag") or h.headers.get("last-modified") or h.headers.get("content-length") or ""
                    except Exception:  # noqa: BLE001 - no stamp: the address alone identifies the file
                        version = ""
                years = content_years(link["title"], link["row"], unquote(link["url"]))
                items.append(ListedItem(
                    item_id="file:" + hashlib.sha1(link["url"].encode()).hexdigest()[:16], kind="file",
                    title=link["title"], version=version, url=final, data_url=link["url"], format=link["format"],
                    category=link["heading"] or page_title, frequency=guess_frequency(link["title"], link["row"]),
                    coverage=coverage(years), fetchable=self.wants(link["format"])))
        return items

    def fetch(self, item: dict) -> Payload:
        return self.file_payload(item["data_url"], item["format"])
