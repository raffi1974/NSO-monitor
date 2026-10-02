"""Step 5 - for platforms we know, list the catalogue precisely through their API.

Crawling HTML only sees what links are on the pages; a platform's API lists everything it holds.
Each recogniser returns the same shape:
    {"platform", "base_url", "items": [...], "suggested_sources": [...], "how_to": {...}, "notes": [...]}
and every item carries the theme ids it belongs to (from config/themes.yaml keywords).

    capmas        Egypt's undocumented JSON API (www.capmas.gov.eg:8080)
    pxweb         PX-Web databanks (e.g. Jordan's JorInfo)
    opendatasoft  Opendatasoft / Huwise open-data portals (e.g. Bahrain, Qatar)
    nada          NADA microdata catalogues (e.g. Comoros)
"""
from __future__ import annotations

import logging
from collections import defaultdict

from ..common.keywords import ThemeMatcher
from ..common.text import clean_text, coverage, extract_years, period_label
from ..common.web import PoliteClient

log = logging.getLogger(__name__)


def _en(translations: list | None, key: str) -> str:
    for tr in translations or []:
        if (tr or {}).get("locale") == "en":
            return (tr.get(key) or "").strip()
    return ""


# ------------------------------------------------------------------------------------------------
# CAPMAS (Egypt)
# ------------------------------------------------------------------------------------------------
CAPMAS_API = "https://www.capmas.gov.eg:8080"
CAPMAS_SITE = "https://www.capmas.gov.eg"
FREQ = {"Annually": "Annual", "Semi-Annually": "Semi-annual", "Bi-Annually": "Biennial", "5 Years": "Every 5 years",
        "10 Years": "Every 10 years", "Non-periodical": "Irregular"}


def capmas(nso_id: str, client: PoliteClient, matcher: ThemeMatcher) -> dict:
    h = {"locale": "en", "Accept": "application/json"}
    get = lambda path, **params: client.get_json(f"{CAPMAS_API}/{path}", params=params or None, headers=h, check_robots=False)  # noqa: E731
    tree = get("api/Subject")["data"]
    subjects = []  # (sub id, main id, english title, themes)
    for main in tree:
        main_themes = set(matcher.match(_en(main.get("subjectTranslations"), "title") or main.get("title", "")))
        for sub in main.get("subSubjects") or []:
            title = _en(sub.get("subjectTranslations"), "title") or sub.get("title", "")
            themes = set(matcher.match(title))
            # CAPMAS's own top level breaks ties: "Economic Census" matches "census" (social) too, but it sits
            # under "Economy", so it is economic only; "Environment" matches nothing and is skipped.
            if themes and main_themes and themes & main_themes:
                themes &= main_themes
            if themes:
                subjects.append((sub["id"], main["id"], title, sorted(themes)))

    items, pubs = [], defaultdict(list)
    sub_of = {}
    for sid, mid, title, themes in subjects:
        page = 1
        while True:
            r = get("api/Publication/Search", SubjectId=sid, PageSize=100, CurrentPage=page)
            for row in r.get("data") or []:
                pubs[row["id"]].append(row)
                sub_of.setdefault(row["id"], (sid, title, themes))
            if page >= int((r.get("page") or {}).get("totalPages") or 1):
                break
            page += 1
    for pid, rows in pubs.items():
        sid, sub_title, themes = sub_of[pid]
        first = rows[0]
        name = _en(first.get("publicationTranslations"), "name") or first.get("name", "")
        per = first.get("periodic") or {}
        freq = _en(per.get("periodicTranslations"), "name") or per.get("name", "")
        years = sorted({int(r["publicationDetail"]["year"]) for r in rows
                        if str((r.get("publicationDetail") or {}).get("year") or "").isdigit()})
        latest = max(rows, key=lambda r: ((r.get("publicationDetail") or {}).get("publishDate") or ""))
        d = latest.get("publicationDetail") or {}
        downloads = [CAPMAS_API + d[k] for k in ("pdfUrl", "excelUrl") if d.get(k)]
        formats = sorted({f for r in rows for f, k in (("PDF", "pdfUrl"), ("XLSX", "excelUrl"))
                          if (r.get("publicationDetail") or {}).get(k)})
        items.append({"kind": "publication", "title": name.strip(), "url": f"{CAPMAS_SITE}/publications/{pid}",
                      "themes": themes, "category": sub_title, "years": years, "coverage": coverage(years),
                      "frequency": FREQ.get(freq, freq), "formats": formats, "downloads": downloads,
                      "editions": len(rows), "latest_release": (d.get("releaseDate") or d.get("publishDate") or "")[:10]})

    indicator_totals, samples = {}, []
    for sid, mid, title, themes in subjects:
        data = (get(f"api/Subject/SubSubjectWithIndicator/{sid}").get("data") or {}).get("publicationWithIndicators") or []
        inds = [(p["id"], i) for p in data for i in (p.get("indicators") or [])]
        indicator_totals[title] = len(inds)
        for pid, ind in inds[:2]:  # a couple of examples per sub-subject, to describe the time series
            try:
                det = get("api/Indicator/IndicatorDetails", indicatorId=ind["indicatorId"], publicationId=pid)["data"]
            except Exception:  # noqa: BLE001
                continue
            start = period_label((det.get("startPeriod") or {}).get("year"))
            end = period_label((det.get("endPeriod") or {}).get("year"))
            unit = _en((det.get("measureUnit") or {}).get("measureUnitTranslations"), "name")
            freq = _en((det.get("periodic") or {}).get("periodicTranslations"), "name")
            samples.append({"sub": title, "themes": themes, "title": _en(ind.get("indicatorTranslations"), "name") or ind.get("name", ""),
                            "coverage": f"{start}-{end}" if start and end else (end and f"up to {end}"), "frequency": FREQ.get(freq, freq),
                            "unit": unit, "url": f"{CAPMAS_SITE}/data/mainSubject/{mid}/subSubject/{sid}/data-visualization/{ind['indicatorId']}"})
        items.append({"kind": "indicator series", "title": f"{title}: {len(inds)} indicator time series (values by year / governorate, as JSON)",
                      "url": f"{CAPMAS_SITE}/data/mainSubject/{mid}/subSubject/{sid}", "themes": themes, "category": title,
                      "formats": ["JSON (API)"], "downloads": [f"{CAPMAS_API}/api/Subject/SubSubjectWithIndicator/{sid}"],
                      "count": len(inds)})

    suggested = []
    theme_ids = sorted({t for _, _, _, ts in subjects for t in ts})
    for tid in theme_ids:
        ids = [sid for sid, _, _, ts in subjects if tid in ts]
        suggested.append({"nso": nso_id, "theme": tid, "method": "api", "connector": "capmas", "enabled": True,
                          "options": {"subject_ids": ids},
                          "notes": "CAPMAS public JSON API (port 8080): publications + indicator time series"})
    how = {"curl": f'curl -H "locale: en" "{CAPMAS_API}/api/Publication/Search?SubjectId=21&PageSize=10&CurrentPage=1"',
           "python": ("import requests\n\nAPI = \"https://www.capmas.gov.eg:8080\"\nheaders = {\"locale\": \"en\"}\n"
                      "# publications (one row per edition) of sub-subject 21 = Vital Statistics\n"
                      "r = requests.get(f\"{API}/api/Publication/Search\", headers=headers,\n"
                      "                 params={\"SubjectId\": 21, \"PageSize\": 100, \"CurrentPage\": 1}, timeout=60)\n"
                      "editions = r.json()[\"data\"]\n"
                      "# one indicator's values (2004 = final divorce judgments by governorate)\n"
                      "r = requests.get(f\"{API}/api/Indicator/IndicatorFilter\", headers=headers,\n"
                      "                 params={\"IndicatorId\": 2004, \"SubSubjectId\": 21}, timeout=60)\n"
                      "series = r.json()[\"data\"]  # [{name: governorate, data: [{year, value}, ...]}, ...]")}
    return {"platform": "CAPMAS JSON API (undocumented)", "base_url": CAPMAS_API, "items": items, "indicator_totals": indicator_totals,
            "indicator_samples": samples, "suggested_sources": suggested, "how_to": how, "documented": False,
            "notes": ["No official API documentation (the Swagger page on port 8080 is not public).",
                      "Files are served from the API host, e.g. https://www.capmas.gov.eg:8080/Publication/Pdf/<id>.pdf",
                      "The server answers HTTP 429 to bursts - keep requests slow (Agent 2 does)."]}


# ------------------------------------------------------------------------------------------------
# PX-Web databanks
# ------------------------------------------------------------------------------------------------
def pxweb(nso_id: str, root: str, client: PoliteClient, matcher: ThemeMatcher, max_tables: int = 400,
          max_requests: int = 80) -> dict:
    budget = {"n": 0}
    tables, skipped = [], []

    def listing(path: str):
        budget["n"] += 1
        return client.get_json(root + path, check_robots=False, retries=1, timeout=20)

    def walk(path: str, crumbs: list[str], depth: int):
        if budget["n"] >= max_requests or len(tables) >= max_tables:
            return
        try:
            nodes = listing(path)
        except Exception as exc:  # noqa: BLE001 - one bad branch (PX-Web trees are occasionally irregular
            skipped.append(path)  # across real sites) must not lose every table found in the others
            log.debug("pxweb: skipping %s: %s", path, exc)
            return
        for node in nodes:
            if node.get("type") == "t":
                tables.append({"path": path + node["id"], "title": node.get("text", ""), "updated": node.get("updated", ""),
                               "crumbs": crumbs})
            elif node.get("type") == "l" and depth < 5:
                walk(path + node["id"] + "/", crumbs + [node.get("text", "")], depth + 1)

    for db in listing(""):
        dbid = db.get("dbid") or db.get("id")
        walk(dbid + "/", [db.get("text", dbid)], 1)
    items, folders = [], defaultdict(set)
    for t in tables:
        themes = matcher.match(t["title"], " ".join(t["crumbs"]))
        years = extract_years(t["title"])
        items.append({"kind": "table", "title": t["title"], "url": root + t["path"], "themes": themes,
                      "category": " / ".join(t["crumbs"][1:]) or t["crumbs"][0], "years": years, "coverage": coverage(years),
                      "formats": ["JSON-stat", "CSV", "XLSX (PX-Web export)"], "downloads": [root + t["path"]],
                      "latest_release": (t["updated"] or "")[:10]})
        for tid in themes:
            folders[tid].add(t["path"].split("/")[0])
    suggested = [{"nso": nso_id, "theme": tid, "method": "api", "connector": "pxweb", "enabled": True,
                  "options": {"api_root": root, "databases": sorted(dbs)}, "notes": "PX-Web API (documented)"}
                 for tid, dbs in folders.items()]
    example = items[0]["url"] if items else root
    how = {"curl": f'curl "{example}"   # table metadata; POST a query to the same URL for the data',
           "python": ("import requests\n\n"
                      f"table = \"{example}\"\n"
                      "meta = requests.get(table, timeout=60).json()          # variables and their values\n"
                      "query = {\"query\": [], \"response\": {\"format\": \"json-stat2\"}}  # empty query = whole table\n"
                      "data = requests.post(table, json=query, timeout=120).json()")}
    notes = [f"Walked {budget['n']} PX-Web folder listings, found {len(tables)} tables"
             + (" (limit reached - there are more)" if len(tables) >= max_tables or budget["n"] >= max_requests else "") + "."]
    if skipped:
        notes.append(f"{len(skipped)} branch(es) did not answer and were skipped, e.g. {skipped[0]}")
    return {"platform": "PX-Web databank (documented API)", "base_url": root, "items": items,
            "suggested_sources": suggested, "how_to": how, "documented": True, "notes": notes}


# ------------------------------------------------------------------------------------------------
# Opendatasoft / Huwise portals
# ------------------------------------------------------------------------------------------------
def opendatasoft(nso_id: str, base: str, client: PoliteClient, matcher: ThemeMatcher, max_datasets: int = 1500) -> dict:
    api = f"{base}/api/explore/v2.1/catalog/datasets"
    rows, offset, total = [], 0, None
    while offset < max_datasets:
        data = client.get_json(api, params={"limit": 100, "offset": offset}, check_robots=False)
        total = data.get("total_count", 0)
        rows += data.get("results") or []
        offset += 100
        if offset >= total:
            break
    items, matched_themes = [], set()
    for row in rows:
        meta = (row.get("metas") or {}).get("default") or {}
        title = meta.get("title") or row.get("dataset_id", "")
        tags = " ".join((meta.get("theme") or []) + (meta.get("keyword") or []))
        themes = matcher.match(title, tags)
        matched_themes.update(themes)
        years = extract_years(title)
        ds = row.get("dataset_id")
        items.append({"kind": "dataset", "title": title, "url": f"{base}/explore/dataset/{ds}/", "themes": themes,
                      "category": ", ".join(meta.get("theme") or []), "years": years, "coverage": coverage(years),
                      "formats": ["CSV", "XLSX", "JSON"], "downloads": [f"{api}/{ds}/exports/csv"],
                      "latest_release": (meta.get("modified") or meta.get("data_processed") or "")[:10],
                      "records": meta.get("records_count")})
    suggested = [{"nso": nso_id, "theme": tid, "method": "api", "connector": "opendatasoft", "enabled": True,
                  "options": {"portal": base}, "notes": "Opendatasoft/Huwise Explore API v2.1 (documented)"}
                 for tid in sorted(matched_themes)]
    ex = items[0]["downloads"][0] if items else api
    how = {"curl": f'curl "{api}?limit=10"',
           "python": ("import requests\n\n"
                      f"catalog = requests.get(\"{api}\", params={{\"limit\": 100}}, timeout=60).json()\n"
                      "datasets = [d[\"dataset_id\"] for d in catalog[\"results\"]]\n"
                      f"csv_text = requests.get(\"{ex}\", timeout=120).text   # a whole dataset as CSV")}
    return {"platform": "Opendatasoft / Huwise portal (documented API)", "base_url": base, "items": items,
            "suggested_sources": suggested, "how_to": how, "documented": True,
            "notes": [f"{len(rows)} of {total} catalogue datasets listed."]}


# ------------------------------------------------------------------------------------------------
# NADA microdata catalogues
# ------------------------------------------------------------------------------------------------
def nada(nso_id: str, base: str, client: PoliteClient, matcher: ThemeMatcher) -> dict:
    api = f"{base}/index.php/api/catalog/search"
    data = client.get_json(api, params={"ps": 200, "page": 1}, check_robots=False)
    rows = (data.get("result") or {}).get("rows") or []
    items, matched = [], set()
    for row in rows:
        themes = matcher.match(row.get("title", ""))
        matched.update(themes)
        ys = [int(y) for y in (row.get("year_start"), row.get("year_end")) if str(y or "").isdigit() and int(y) > 0]
        items.append({"kind": "survey (microdata)", "title": clean_text(row.get("title")), "url": f"{base}/index.php/catalog/{row.get('id')}",
                      "themes": themes, "category": row.get("type", "survey"), "years": ys, "coverage": coverage(sorted(set(ys))),
                      "formats": ["microdata (registration may be needed)"], "downloads": [],
                      "latest_release": (row.get("changed") or row.get("created") or "")[:10]})
    suggested = [{"nso": nso_id, "theme": tid, "method": "api", "connector": "nada", "enabled": True,
                  "options": {"catalog": base}, "notes": "NADA catalogue API (documented) - new surveys, catalogue only"}
                 for tid in sorted(matched)]
    how = {"curl": f'curl "{api}?ps=10"',
           "python": f"import requests\n\nrows = requests.get(\"{api}\", params={{\"ps\": 100}}, timeout=60).json()[\"result\"][\"rows\"]"}
    return {"platform": "NADA microdata catalogue (documented API)", "base_url": base, "items": items,
            "suggested_sources": suggested, "how_to": how, "documented": True, "notes": [f"{len(rows)} surveys listed."]}
