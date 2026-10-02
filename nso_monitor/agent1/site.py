"""Step 1 - is the website up, is there an English version, is it blocked, what is it built with?"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..common.browser import Browser
from ..common.config import Site
from ..common.html import looks_like_spa
from ..common.web import PoliteClient

BLOCK_SIGNS = [
    ("Cloudflare firewall (HTTP 403) - this network is blocked", r"Attention Required! \| Cloudflare|Sorry, you have been blocked"),
    # only the challenge page's title - Cloudflare also injects challenge scripts into normal pages
    ("Cloudflare bot challenge", r"<title>\s*Just a moment\.\.\.\s*</title>"),
    ("local web filter (e.g. the UN network) blocks this site", r"The URL you requested has been blocked|Web Page Blocked"),
    ("parked domain - no statistics website at this address", r"Buy this domain|domain is for sale|Seo\.Domains"),
    ("no website at this address (hosting placeholder)", r"Site not found|Site Not Found|Default Web Site Page|Index of /"),
]
PLATFORMS = [
    ("WordPress", r"wp-content|wp-json"),
    ("Drupal", r"Drupal\.settings|/sites/default/files|drupal\.js"),
    ("Joomla", r"/media/jui/|option=com_|Joomla!"),
    ("SharePoint", r"_layouts/15|SharePoint"),
    ("ASP.NET WebForms", r"__VIEWSTATE"),
    ("Next.js app", r"/_next/|__NEXT_DATA__"),
    ("PX-Web databank", r"pxweb"),
    (".Stat Suite (SDMX)", r"dotstat|data-explorer"),
    ("Opendatasoft / Huwise portal", r"opendatasoft|huwise"),
    ("NADA microdata catalogue", r"/index\.php/catalog|nada\."),
    ("Knoema data portal", r"knoema"),
    ("Tableau dashboards", r"public\.tableau\.com"),
    ("Power BI dashboards", r"app\.powerbi\.com"),
]
ENGLISH_LINK = re.compile(r"(/en(/|$|-)|[?&]lang(uage)?=en\b|_en\.aspx|/english)", re.I)


def detect_block(html: str, status: int | None) -> str:
    for label, pattern in BLOCK_SIGNS:
        if re.search(pattern, html or "", re.I):
            return label
    return "HTTP %s" % status if status and status >= 400 else ""


def links_from_html(html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
            out.append((a.get_text(" ", strip=True) or a.get("title", ""), urljoin(base, href)))
    return out


def check_site(site: Site, client: PoliteClient, browser: Browser) -> dict:
    """Returns the 'site' part of the diagnosis plus the HTML/links the crawler starts from."""
    info = {"url": site.url, "reachable": False, "status": None, "final_url": "", "title": "", "lang": "",
            "english": site.english or "", "english_url": "", "platforms": [], "blocked": "", "rendered": False,
            "insecure_tls": False, "notes": [], "html": "", "links": []}
    html, status, final = "", None, site.url
    try:
        r = client.get(site.url, check_robots=False, retries=1, timeout=30)
        status, final = r.status_code, str(r.url)
        html = r.text if "html" in r.headers.get("content-type", "") else ""
        if r.headers.get("server", "").lower() == "cloudflare":
            info["platforms"].append("behind Cloudflare")
    except Exception as exc:  # noqa: BLE001 - recorded, the browser may still get through
        info["notes"].append(f"plain HTTP request failed: {type(exc).__name__}: {str(exc)[:150]}")
    info["insecure_tls"] = urlsplit(site.url).netloc in client.insecure_hosts

    blocked = detect_block(html, status) if (html or status) else "no response"
    if blocked or not html or looks_like_spa(html):
        page = browser.render(site.url)
        if page.get("html") and not detect_block(page["html"], page.get("status")):
            html, status, final = page["html"], page.get("status"), page.get("final_url") or final
            info["rendered"] = True
            blocked = ""
        elif page.get("error") and not html:
            info["notes"].append(f"browser could not open it either: {page['error']}")
        elif page.get("html"):
            blocked = detect_block(page["html"], page.get("status")) or blocked

    info.update(status=status, final_url=final, blocked=blocked, html=html)
    info["reachable"] = bool(html) and not blocked
    if not info["reachable"]:
        return info

    soup = BeautifulSoup(html, "html.parser")
    info["title"] = soup.title.get_text(" ", strip=True)[:150] if soup.title else ""
    info["lang"] = (soup.html.get("lang") if soup.html else "") or ""
    info["links"] = links_from_html(html, final)
    for label, pattern in PLATFORMS:
        if re.search(pattern, html, re.I) and label not in info["platforms"]:
            info["platforms"].append(label)
    if looks_like_spa(html) or info["rendered"]:
        info["platforms"].append("JavaScript-rendered pages")

    if site.english in ("yes", "in-app"):
        info["english_url"] = final
    else:
        candidates = [u for t, u in info["links"] if t.strip().lower() in ("english", "en", "eng") or ENGLISH_LINK.search(u)]
        same_site = [u for u in candidates if urlsplit(u).hostname == urlsplit(final).hostname]
        info["english_url"] = (same_site or [""])[0]
        if info["english_url"]:
            info["notes"].append(f"possible English page found: {info['english_url']}")
    return info
