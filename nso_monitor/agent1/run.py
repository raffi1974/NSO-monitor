"""Runs Agent 1's steps for each website and writes the reports (called by agent1_diagnose.py)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from ..common.browser import Browser
from ..common.config import OUTPUT_DIR, browser_channel, load_agent1, load_themes
from ..common.keywords import ThemeMatcher
from ..common.web import PoliteClient
from . import api_probe, crawl, recognizers, report, sniff
from .site import check_site

log = logging.getLogger("nso_monitor.agent1")


def _verdict(probe: dict, sniffed: dict | None, recs: list[dict]) -> dict:
    data_apis = [p for p in probe["documented"] if not p["content_api"]]
    content_apis = [p for p in probe["documented"] if p["content_api"]]
    endpoints = (sniffed or {}).get("endpoints") or []
    working = [e for e in endpoints if e.get("works_from_python")]
    hidden = [r for r in recs if not r.get("documented")]
    out = {"docs": [], "how_to": {}, "endpoints": endpoints, "js_findings": (sniffed or {}).get("scripts") or {},
           "doc_links": probe["doc_links"]}
    if data_apis:
        out.update(status="documented", python_access="yes",
                   summary="Documented API: " + "; ".join(f"{p['platform']} at {p['base_url']} ({p['evidence']})" for p in data_apis),
                   docs=list(dict.fromkeys(p["docs_url"] for p in data_apis)))
    elif hidden or working:
        where = hidden[0]["base_url"] if hidden else urlsplit(working[0]["example_url"]).netloc
        out.update(status="undocumented", python_access="yes",
                   summary=f"No official documentation, but the website's own data API at {where} answers plain Python "
                           f"requests (found by API sniffing).")
    elif endpoints:
        needs_auth = any(e.get("needs_auth") for e in endpoints)
        out.update(status="undocumented", python_access="partial" if needs_auth else "no",
                   summary=f"The website calls {len(endpoints)} internal data endpoint(s), but they did not answer outside "
                           "the browser" + (" (they need a login/session token)" if needs_auth else "") + ".")
    elif content_apis:
        out.update(status="content API only", python_access="partial",
                   summary="No statistics API; the site's content-management API ("
                           + ", ".join(p["platform"] for p in content_apis) + ") can list pages and uploaded files.",
                   docs=[p["docs_url"] for p in content_apis])
    else:
        out.update(status="none", python_access="no",
                   summary="No API found (no documented platform, no data calls seen while browsing). "
                           "Data is published as files/pages only: Agent 2 would use the 'scrape' connector.")
    rec_how = next((r["how_to"] for r in recs if r.get("how_to")), None)
    if rec_how:
        out["how_to"] = rec_how
    elif working:
        out["how_to"] = sniff.how_to(working[0])
    elif content_apis:
        base = content_apis[0]["base_url"]
        out["how_to"] = {"curl": f'curl "{base}/wp-json/wp/v2/media?per_page=20"',
                         "python": f"import requests\nfiles = requests.get(\"{base}/wp-json/wp/v2/media\", params={{\"per_page\": 50}}, timeout=60).json()"}
    return out


def diagnose(site, settings: dict, themes: dict, matcher: ThemeMatcher, client: PoliteClient, browser: Browser) -> dict:
    t0 = time.monotonic()
    d = {"id": site.id, "country": site.country, "nso": site.nso, "url": site.url, "english_config": site.english,
         "languages": site.languages, "config_notes": site.notes,
         "checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
         "themes": {tid: {"label": t.label, "items": []} for tid, t in themes.items()},
         "limitations": [], "recognized": [], "suggested_sources": [], "indicator_samples": []}

    log.info("[%s] 1/5 site check", site.id)
    info = check_site(site, client, browser)
    d["site"] = {k: v for k, v in info.items() if k not in ("html", "links")}
    if not info["reachable"]:
        d["limitations"].append(f"Website not reachable from this network: {info['blocked'] or 'no response'}. "
                                "Run Agent 1 from another network (or ask the Claude subagent to investigate).")

    log.info("[%s] 2/5 documented-API probe", site.id)
    probe = api_probe.probe(site, info, client)

    crawled = {"items": [], "formats": {}, "files_total": 0, "pages_crawled": 0, "pages_rendered": 0}
    sniffed = None
    if info["reachable"]:
        log.info("[%s] 3/5 crawl for theme datasets and files", site.id)
        crawled = crawl.crawl(site, info, client, browser, matcher, settings)
        log.info("[%s] 4/5 API sniffing in a browser", site.id)
        pages = [info["final_url"]] + [it["url"] for it in crawled["items"] if it["kind"] == "page"]
        try:
            sniffed = sniff.sniff(pages, client, browser, int(settings.get("sniff_pages", 4)))
        except Exception as exc:  # noqa: BLE001
            d["limitations"].append(f"API sniffing failed: {type(exc).__name__}: {str(exc)[:150]}")

    log.info("[%s] 5/5 platform catalogues", site.id)
    jobs = []
    if "capmas.gov.eg" in (urlsplit(info.get("final_url") or site.url).hostname or ""):
        jobs.append(("CAPMAS API", lambda: recognizers.capmas(site.id, client, matcher)))
    for p in probe["documented"]:
        if p["platform"] == "PX-Web":
            jobs.append(("PX-Web", lambda p=p: recognizers.pxweb(site.id, p["base_url"], client, matcher)))
        elif p["platform"] == "Opendatasoft / Huwise":
            jobs.append(("Opendatasoft", lambda p=p: recognizers.opendatasoft(site.id, p["base_url"], client, matcher)))
        elif p["platform"] == "NADA":
            jobs.append(("NADA", lambda p=p: recognizers.nada(site.id, p["base_url"], client, matcher)))
    recs = []
    for name, job in jobs:
        try:
            rec = job()
        except Exception as exc:  # noqa: BLE001 - one platform failing must not lose the rest of the report
            d["limitations"].append(f"{name} catalogue listing failed: {type(exc).__name__}: {str(exc)[:150]}")
            continue
        recs.append(rec)
        d["recognized"].append({k: rec[k] for k in ("platform", "base_url", "notes")} | {"items": len(rec["items"])})
        d["suggested_sources"] += rec["suggested_sources"]
        d["indicator_samples"] += rec.get("indicator_samples", [])
        for it in rec["items"]:
            for tid in it.get("themes") or []:
                d["themes"][tid]["items"].append(it)

    for it in crawled["items"]:
        for tid in it.get("themes") or []:
            d["themes"][tid]["items"].append(it)
    d["files"] = {"formats": crawled["formats"], "total": crawled["files_total"],
                  "pages_crawled": crawled["pages_crawled"], "pages_rendered": crawled["pages_rendered"]}
    d["api"] = _verdict(probe, sniffed, recs)
    d["sniff_pages"] = (sniffed or {}).get("pages_opened", [])

    # Themes with files on the pages but no API source: suggest the scrape connector (disabled until reviewed).
    covered = {s["theme"] for s in d["suggested_sources"]}
    for tid, th in d["themes"].items():
        if tid in covered:
            continue
        files = [it for it in th["items"] if it["kind"] == "file"]
        if files:
            pages = list(dict.fromkeys(it["page_url"] for it in files))[:5]
            d["suggested_sources"].append({"nso": site.id, "theme": tid, "method": "scrape", "connector": "scrape",
                                           "enabled": False, "pages": pages, "render_js": "auto",
                                           "notes": f"{len(files)} {th['label'].lower()} file(s) found on these pages - review before enabling"})
    if info["reachable"] and not any(th["items"] for th in d["themes"].values()):
        d["limitations"].append("No theme dataset or file was found automatically - the data may sit behind search forms, "
                                "dashboards (Tableau/Power BI) or an app. Ask the Claude subagent (/diagnose) to dig deeper.")
    if site.english == "no":
        d["limitations"].append("No English version: the non-English pages were analysed (keywords cover Arabic and French).")
    d["duration_s"] = round(time.monotonic() - t0, 1)
    return d


def _diagnose_one(site, settings, themes, matcher, client, out_dir) -> dict:
    """One site, start to finish, including its own report write. Runs inside a worker thread, so it
    opens and closes its OWN Browser: Playwright's sync API is not safe to share across threads (each
    `sync_playwright()` context belongs to the thread that created it), unlike `client` (httpx.Client is
    documented thread-safe, and PoliteClient's per-host rate limiting is lock-protected - see web.py) -
    which is exactly why one shared client makes sense here: 22 different sites are 22 different hosts,
    so their rate limits are independent even while checked concurrently."""
    log.info("=== %s - %s ===", site.id, site.nso)
    try:
        with Browser(channel=browser_channel(settings), timeout_s=settings["timeout_seconds"]) as browser:
            d = diagnose(site, settings, themes, matcher, client, browser)
    except Exception as exc:  # noqa: BLE001 - keep going with the other sites
        log.exception("[%s] diagnostics crashed", site.id)
        d = {"id": site.id, "country": site.country, "nso": site.nso, "url": site.url, "english_config": site.english,
             "checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "site": {}, "api": {"status": "unknown"},
             "themes": {tid: {"label": t.label, "items": []} for tid, t in themes.items()},
             "limitations": [f"Diagnostics crashed: {type(exc).__name__}: {exc}"], "suggested_sources": []}
    path = report.write_site(d, out_dir)
    log.info("[%s] report written: %s", site.id, path.name)
    return d


async def _run_async(ids: list[str] | None) -> list[dict]:
    cfg, themes = load_agent1(), load_themes()
    settings = cfg.settings
    matcher = ThemeMatcher(themes)
    out_dir = OUTPUT_DIR / "agent1"
    sites = cfg.select(ids)
    # Bounded, not one-task-per-site unconditionally: each site opens its own Chromium (~150-250MB+),
    # so running all of them at once risks exhausting memory on an ordinary machine. Concurrency is
    # across sites only (22 independent hosts) - each site's own steps stay sequential (site check ->
    # probe -> crawl -> sniff -> recognise each depend on the previous step's result for THAT site).
    workers = max(1, min(int(settings.get("max_parallel_sites", 5)), len(sites)))
    log.info("diagnosing %d site(s), up to %d concurrently", len(sites), workers)

    with PoliteClient(delay=settings["request_delay_seconds"], timeout=settings["timeout_seconds"]) as client:
        loop = asyncio.get_running_loop()
        # _diagnose_one() itself is unchanged sync code (it opens its own Browser per call, exactly as
        # for a plain thread pool - Playwright's sync API still needs one sync_playwright() per thread,
        # asyncio or not). What changes is the ORCHESTRATION: asyncio.gather + run_in_executor schedules
        # and awaits the 22 site-jobs, instead of a bare ThreadPoolExecutor + as_completed(). The pool is
        # passed explicitly (rather than asyncio.to_thread(), which only uses the loop's default executor)
        # so its size is exactly `workers`, matching the memory-bound reasoning above precisely.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            tasks = [loop.run_in_executor(pool, _diagnose_one, s, settings, themes, matcher, client, out_dir)
                    for s in sites]
            results = list(await asyncio.gather(*tasks))  # gather() preserves input order regardless of completion order
    report.write_summary(out_dir)
    report.write_suggestions(out_dir)
    return results


def run(ids: list[str] | None = None) -> list[dict]:
    """Sync entry point (agent1_diagnose.py, the /diagnose subagent): runs the asyncio orchestration
    above in its own event loop, so callers don't need to be async themselves."""
    return asyncio.run(_run_async(ids))
