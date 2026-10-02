"""Step 3 - API SNIFFING: find the data API a website uses behind the scenes, and test it from Python.

1. A real browser opens a few pages and records every background data call (XHR / fetch) that returns
   JSON, XML or CSV.
2. The site's JavaScript files are scanned for API base URLs and endpoint paths - this is how Egypt's
   hidden API on port 8080 was found.
3. Each recorded call is REPLAYED with plain Python (no browser, no cookies): if it still answers,
   the NSO's data can be downloaded with a few lines of Python.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from ..common.browser import Browser, is_noise
from ..common.web import PoliteClient

_ID = re.compile(r"/\d+(?=/|$)")
_REPLAY_HEADERS = ("accept", "accept-language", "content-type", "locale", "x-requested-with")
_BASE_KEYS = re.compile(r"(?:API[_A-Z]*URL|BASE_?URL|baseURL|apiUrl|apiBase|API_ENDPOINT[_A-Z]*)\s*[:=]\s*[\"'`]"
                        r"(https?://[^\"'`\s]+)")
_API_PATH = re.compile(r"[\"'`](/?api/[A-Za-z0-9_\-/{}.]{2,120})[\"'`]")
_API_URL = re.compile(r"https?://[A-Za-z0-9.\-]+(?::\d+)?/[^\s\"'`<>()]*\bapi\b[^\s\"'`<>()]*", re.I)


def template(url: str) -> str:
    """Same endpoint shape = one entry: /Publication/42 and /Publication/57 -> /Publication/{id}."""
    p = urlsplit(url)
    return f"{p.netloc}{_ID.sub('/{id}', p.path)}"


def scan_scripts(scripts: list[tuple[str, str]]) -> dict:
    base_urls, paths, api_urls = [], [], []
    for _, body in scripts:
        base_urls += _BASE_KEYS.findall(body)
        paths += [p.lstrip("/") for p in _API_PATH.findall(body)]
        api_urls += [u for u in _API_URL.findall(body) if not is_noise(u)]
    uniq = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    return {"scripts_scanned": len(scripts), "base_urls": uniq(base_urls)[:20],
            "endpoint_paths": uniq(paths)[:200], "api_urls": uniq(api_urls)[:40]}


def _replay(ep: dict, client: PoliteClient) -> None:
    headers = {k: v for k, v in ep.get("headers", {}).items()
               if k.lower() in _REPLAY_HEADERS or (k.lower().startswith("x-") and k.lower() != "x-csrf-token")}
    ep["python_headers"] = headers
    ep["needs_auth"] = any(k.lower() in ("authorization", "x-csrf-token", "x-xsrf-token") for k in ep.get("headers", {}))
    try:
        if ep["method"] == "GET":
            r = client.get(ep["example_url"], headers=headers, check_robots=False, retries=0, timeout=20)
        elif ep["method"] == "POST":
            r = client.request("POST", ep["example_url"], headers=headers, content=ep.get("post_data", ""),
                               check_robots=False, retries=0, timeout=20)
        else:
            ep["works_from_python"] = False
            return
        ctype = r.headers.get("content-type", "")
        ep["replay_status"] = r.status_code
        ep["works_from_python"] = (r.status_code < 400 and len(r.content) > 2
                                   and any(t in ctype for t in ("json", "xml", "csv", "text/plain")))
    except Exception as exc:  # noqa: BLE001 - a failing replay is a finding, not an error
        ep["replay_error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
        ep["works_from_python"] = False


def sniff(urls: list[str], client: PoliteClient, browser: Browser, max_pages: int = 4) -> dict:
    urls = list(dict.fromkeys(u for u in urls if u))[:max_pages]
    cap = browser.capture(urls, explore_links=max(0, max_pages - len(urls)))
    grouped: dict[tuple, dict] = {}
    for c in cap["calls"]:
        ctype = (c.get("content_type") or "").lower()
        if ctype.startswith("image/") or not any(t in ctype for t in ("json", "xml", "csv")):
            continue  # image/svg+xml contains "xml" but is a picture, not data
        key = (c["method"], template(c["url"]))
        g = grouped.setdefault(key, {"method": c["method"], "template": key[1], "example_url": c["url"], "count": 0,
                                     "content_type": c.get("content_type", ""), "headers": c.get("headers", {}),
                                     "post_data": c.get("post_data", ""), "sample": c.get("sample", ""),
                                     "size": c.get("size") or 0})
        g["count"] += 1
        if len(c["url"]) < len(g["example_url"]):
            g["example_url"] = c["url"]
    endpoints = sorted(grouped.values(), key=lambda g: -(g.get("size") or 0))[:25]
    for ep in endpoints[:12]:
        _replay(ep, client)
    for ep in endpoints:
        ep.pop("headers", None)
    return {"pages_opened": cap["pages"], "calls_seen": len(cap["calls"]), "endpoints": endpoints,
            "scripts": scan_scripts(cap["scripts"])}


def how_to(ep: dict) -> dict:
    """Ready-to-copy curl and Python examples for one working endpoint."""
    headers = ep.get("python_headers") or {}
    h = " ".join(f'-H "{k}: {v}"' for k, v in headers.items())
    if ep["method"] == "POST":
        curl = f"curl -X POST {h} --data '{ep.get('post_data', '')}' \"{ep['example_url']}\""
        call = f"requests.post(url, headers=headers, data={ep.get('post_data', '')!r}, timeout=60)"
    else:
        curl = f"curl {h} \"{ep['example_url']}\"".replace("  ", " ")
        call = "requests.get(url, headers=headers, timeout=60)"
    python = (f"import requests\n\nurl = \"{ep['example_url']}\"\nheaders = {json.dumps(headers)}\n"
              f"response = {call}\nresponse.raise_for_status()\ndata = response.json()")
    return {"curl": curl, "python": python}
