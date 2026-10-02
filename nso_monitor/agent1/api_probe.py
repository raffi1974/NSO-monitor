"""Step 2 - does the NSO have a DOCUMENTED API?

Asks each server the well-known questions a statistics platform answers (every hit is evidence from a
real response - nothing is assumed about a site in advance):

    SDMX REST (.Stat Suite)   /rest/dataflow/all/all/latest
    PX-Web databank           <install>/api/v1/<lang>/            (derived from any /pxweb/ link)
    Opendatasoft / Huwise     /api/explore/v2.1/catalog/datasets
    CKAN open-data portal     /api/3/action/status_show
    NADA microdata catalogue  /index.php/api/catalog/search
    Swagger / OpenAPI         /swagger/v1/swagger.json, /openapi.json, ...
    WordPress / Drupal        /wp-json/ , /jsonapi   (content APIs: list pages and uploaded files)

It also lists the site's own links that talk about an API, open data, a databank or SDMX.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from ..common.config import Site
from ..common.keywords import normalize
from ..common.web import PoliteClient

PROBES = [
    ("SDMX REST", ["/rest/dataflow/all/all/latest", "/sdmx/rest/dataflow/all/all/latest"]),
    ("Opendatasoft / Huwise", ["/api/explore/v2.1/catalog/datasets?limit=1"]),
    ("CKAN", ["/api/3/action/status_show"]),
    ("NADA", ["/index.php/api/catalog/search?ps=1"]),
    ("OpenAPI / Swagger", ["/swagger/v1/swagger.json", "/swagger.json", "/openapi.json", "/v3/api-docs", "/api-docs"]),
    ("WordPress REST", ["/wp-json/"]),
    ("Drupal JSON:API", ["/jsonapi"]),
]
DOCS = {
    "SDMX REST": "https://github.com/sdmx-twg/sdmx-rest/wiki",
    "PX-Web": "https://www.scb.se/en/services/open-data-api/api-for-the-statistical-database/",
    "Opendatasoft / Huwise": "{base}/api/explore/v2.1/console",
    "CKAN": "https://docs.ckan.org/en/latest/api/",
    "NADA": "https://nada.ihsn.org/",
    "WordPress REST": "https://developer.wordpress.org/rest-api/reference/",
    "Drupal JSON:API": "https://www.drupal.org/docs/core-modules-and-themes/core-modules/jsonapi-module",
}
# Content APIs list pages/files, not statistics: useful for spotting new publications, weaker than a data API.
CONTENT_APIS = {"WordPress REST", "Drupal JSON:API"}
# Matched against normalize()d link text (lower case, no accents, Arabic letters folded: ة -> ه).
API_WORDS = re.compile(r"\b(api|apis|developers?|open ?data|sdmx|databank|data ?bank|data ?portal|web ?services?|"
                       r"donnees ouvertes|base de donnees)\b|بيانات مفتوحه|واجهه برمجه|قاعده البيانات|قواعد البيانات")


def _base(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _classify(platform: str, r) -> str | None:
    """Evidence string when the response really is that platform, else None."""
    if r.status_code >= 400:
        return None
    ctype = r.headers.get("content-type", "").lower()
    text = r.text[:3000] if r.content else ""
    try:
        data = r.json() if ("json" in ctype or text.lstrip()[:1] in "[{") else None
    except (ValueError, json.JSONDecodeError):
        data = None
    if platform == "SDMX REST":
        return "SDMX structure message listing dataflows" if ("dataflow" in text.lower() and ("xml" in ctype or data)) else None
    if platform == "Opendatasoft / Huwise" and isinstance(data, dict) and "total_count" in data:
        return f"catalogue of {data['total_count']} datasets"
    if platform == "CKAN" and isinstance(data, dict) and "ckan_version" in json.dumps(data)[:2000]:
        return "CKAN Action API"
    if platform == "NADA" and isinstance(data, dict) and isinstance(data.get("result"), dict) and "rows" in data["result"]:
        return f"catalogue of {data['result'].get('total', '?')} surveys"
    if platform == "OpenAPI / Swagger" and isinstance(data, dict) and ({"openapi", "swagger"} & set(data)):
        return f"OpenAPI specification: {data.get('info', {}).get('title', '')}".strip()
    if platform == "WordPress REST" and isinstance(data, dict) and "namespaces" in data:
        return "WordPress REST API (lists posts, pages and uploaded files)"
    if platform == "Drupal JSON:API" and isinstance(data, dict) and "jsonapi" in data:
        return "Drupal JSON:API (lists content)"
    return None


def pxweb_roots(urls: list[str]) -> list[str]:
    """.../Databank/pxweb/en/  ->  .../Databank/api/v1/en/  (PX-Web installs serve their API next to the UI)."""
    roots = []
    for u in urls:
        m = re.search(r"^(.*?)/pxweb/([a-z]{2})(/|$)", u, re.I)
        if m:
            root = f"{m.group(1)}/api/v1/{m.group(2).lower()}/"
            if root not in roots:
                roots.append(root)
    return roots


def probe(site: Site, site_info: dict, client: PoliteClient) -> dict:
    links = site_info.get("links") or []
    bases = [_base(site_info.get("final_url") or site.url)] + [_base(p["url"]) for p in site.portals if p.get("url")]
    bases = list(dict.fromkeys(bases))
    found, tried = [], 0

    for base in bases:
        for platform, paths in PROBES:
            for path in paths:
                tried += 1
                try:  # one short attempt: a probe that doesn't answer quickly is itself the answer
                    r = client.get(base + path, check_robots=False, retries=0, timeout=12,
                                   headers={"Accept": "application/json, application/xml"})
                except Exception:  # noqa: BLE001 - an unreachable path is a normal answer here
                    continue
                evidence = _classify(platform, r)
                if evidence:
                    docs = DOCS.get(platform, base + path).replace("{base}", base)
                    found.append({"platform": platform, "base_url": base, "endpoint": base + path,
                                  "docs_url": docs, "evidence": evidence, "content_api": platform in CONTENT_APIS})
                    break

    candidate_urls = [u for _, u in links] + [p["url"] for p in site.portals if p.get("url")]
    for root in pxweb_roots(candidate_urls):
        tried += 1
        try:
            r = client.get(root, check_robots=False, retries=0, timeout=20)
            data = r.json() if r.status_code < 400 else None
        except Exception:  # noqa: BLE001
            data = None
        if isinstance(data, list) and data and isinstance(data[0], dict) and ("dbid" in data[0] or "id" in data[0]):
            found.append({"platform": "PX-Web", "base_url": root, "endpoint": root, "docs_url": DOCS["PX-Web"],
                          "evidence": f"PX-Web API listing {len(data)} database(s)", "content_api": False})

    doc_links = []
    for text, url in links:
        if API_WORDS.search(normalize(text)) or re.search(r"/(api|developer|opendata|open-data|sdmx|databank)(/|$|\?)", url, re.I):
            if url not in [d["url"] for d in doc_links]:
                doc_links.append({"text": (text or "")[:80], "url": url})
    return {"documented": found, "doc_links": doc_links[:12], "probes_tried": tried}
