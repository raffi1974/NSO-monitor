"""A real (headless) browser via Playwright, for the two jobs plain HTTP can't do:

* render(url)  - load JavaScript-only pages and return the HTML a human would see
* capture(...) - record the API calls (XHR/fetch) a page makes in the background: this is "API sniffing"

Locally it drives the Edge already installed on Windows (no download). On GitHub the workflow sets
NSO_BROWSER_CHANNEL="" so Playwright's own Chromium is used instead.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin, urlsplit

log = logging.getLogger(__name__)

# Third-party services that are never an NSO's data API (analytics, ads, social media, maps, fonts...).
NOISE_HOSTS = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com", "doubleclick.net", "google.com",
    "gstatic.com", "googleapis.com", "facebook.com", "facebook.net", "twitter.com", "x.com", "twimg.com",
    "youtube.com", "ytimg.com", "linkedin.com", "instagram.com", "hotjar.com", "clarity.ms", "bing.com",
    "cloudflareinsights.com", "cloudflare.com", "jsdelivr.net", "unpkg.com", "fontawesome.com", "addthis.com",
    "sharethis.com", "tiktok.com", "snapchat.com", "api.ipify.org", "noembed.com", "vimeo.com",
    "openstreetmap.org", "readspeaker.com", "recaptcha.net", "hcaptcha.com", "gravatar.com", "wp.com",
)


class BrowserUnavailable(RuntimeError):
    pass


def is_noise(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in NOISE_HOSTS)


class Browser:
    """Use as `with Browser(channel="msedge") as b: b.render(url)`. Starts lazily on first use."""

    def __init__(self, channel: str | None = "msedge", headless: bool = True, timeout_s: float = 45):
        self.channel, self.headless, self.timeout_ms = channel, headless, int(timeout_s * 1000)
        self._pw = self._browser = self._context = None

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _start(self):
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        errors = []
        for channel in ([self.channel] if self.channel else []) + [None]:
            try:
                kw = {"headless": self.headless}
                if channel:
                    kw["channel"] = channel
                self._browser = self._pw.chromium.launch(**kw)
                break
            except Exception as exc:  # noqa: BLE001 - try the next browser
                errors.append(f"{channel or 'bundled chromium'}: {str(exc)[:120]}")
        if self._browser is None:
            self._pw.stop()
            raise BrowserUnavailable("No browser for Playwright. On Windows keep browser_channel: msedge; "
                                     "elsewhere run `python -m playwright install chromium`. " + " | ".join(errors))
        self._context = self._browser.new_context(locale="en-US", ignore_https_errors=True)

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = None

    def _goto(self, page, url: str):
        response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, 15000))
        except Exception:  # noqa: BLE001 - busy pages never go idle; the DOM is enough
            pass
        return response

    def render(self, url: str) -> dict:
        """{url, final_url, status, title, lang, html, text, links: [(text, absolute_url)], error}."""
        self._start()
        page = self._context.new_page()
        out = {"url": url}
        try:
            response = self._goto(page, url)
            out.update(final_url=page.url, status=response.status if response else None, title=page.title(),
                       lang=page.evaluate("document.documentElement.lang || ''"), html=page.content(),
                       text=page.evaluate("document.body ? document.body.innerText : ''"))
            out["links"] = [(t.strip(), urljoin(page.url, h)) for t, h in page.eval_on_selector_all(
                "a[href]", "els => els.map(e => [e.innerText || e.title || '', e.getAttribute('href')])")
                if h and not h.startswith(("javascript:", "mailto:", "tel:", "#"))]
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            page.close()
        return out

    def capture(self, urls: list[str], explore_links: int = 0) -> dict:
        """Open `urls` (+ up to `explore_links` same-site links found on the first page) and record every
        background data call. Returns {"pages": [...], "calls": [...], "scripts": [(url, body_text)]}."""
        self._start()
        page = self._context.new_page()
        calls, scripts, pages = [], [], []

        def on_response(resp):
            req = resp.request
            try:
                rtype, url = req.resource_type, resp.url
                if is_noise(url):
                    return
                ctype = resp.headers.get("content-type", "")
                if rtype == "script" and len(scripts) < 12:
                    try:
                        body = resp.body()
                        if len(body) < 12_000_000:
                            scripts.append((url, body.decode("utf-8", errors="replace")))
                    except Exception:  # noqa: BLE001
                        pass
                    return
                if rtype not in ("xhr", "fetch"):
                    return
                item = {"url": url, "method": req.method, "status": resp.status, "content_type": ctype,
                        "page": page.url, "post_data": (req.post_data or "")[:1000] if req.method != "GET" else "",
                        "headers": {k: v for k, v in req.headers.items()
                                    if k.lower() not in ("cookie", "user-agent", "referer", "accept-encoding")
                                    and not k.lower().startswith("sec-")}}
                if any(t in ctype for t in ("json", "xml", "csv", "text/plain")):
                    try:
                        body = resp.body()
                        item["size"] = len(body)
                        item["sample"] = body[:800].decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001 - body gone after navigation
                        item["size"] = None
                calls.append(item)
            except Exception:  # noqa: BLE001 - a listener must never break the page
                pass

        page.on("response", on_response)
        queue = list(dict.fromkeys(urls))
        for i, url in enumerate(queue):
            entry = {"url": url}
            try:
                response = self._goto(page, url)
                entry.update(final_url=page.url, status=response.status if response else None, title=page.title())
                if i == 0 and explore_links:
                    host = urlsplit(page.url).hostname
                    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                    for h in hrefs:
                        h = h.split("#")[0]
                        if (urlsplit(h).hostname == host and h not in queue and len(queue) < len(urls) + explore_links
                                and not re.search(r"\.(pdf|xlsx?|zip|docx?|jpe?g|png)$", h, re.I)):
                            queue.append(h)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            pages.append(entry)
            time.sleep(0.5)
        page.close()
        return {"pages": pages, "calls": calls, "scripts": scripts}
