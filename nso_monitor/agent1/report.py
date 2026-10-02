"""Step 6 - write the results to output/agent1/.

    <ID>.txt                         the human-readable diagnostics report (the baseline)
    <ID>.json                        the same information, machine-readable (used by the dashboard)
    SUMMARY.txt                      one line per NSO, rebuilt from every <ID>.json present
    suggested_agent2_sources.yaml    ready-to-paste entries for config/agent2_sources.yaml
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import yaml

W = 100
ACCESS = {"yes": "works from Python (no login needed)", "partial": "partly - some calls need a browser session",
          "no": "not callable from Python", "unknown": "not determined"}
MAX_LIST = 60


def _wrap(text: str, indent: int = 5) -> list[str]:
    return textwrap.wrap(str(text), W - indent, initial_indent=" " * indent, subsequent_indent=" " * (indent + 2)) or [" " * indent]


def _item_line(it: dict) -> list[str]:
    bits = [it.get("title") or "(untitled)"]
    for key in ("frequency", "coverage"):
        if it.get(key):
            bits.append(str(it[key]))
    if it.get("editions"):
        bits.append(f"{it['editions']} editions")
    if it.get("formats"):
        bits.append(", ".join(it["formats"]))
    if it.get("latest_release"):
        bits.append(f"latest {it['latest_release']}")
    lines = _wrap("- " + " | ".join(bits))
    link = (it.get("downloads") or [None])[0] or it.get("url")
    if link:
        lines += _wrap(link, 7)
    return lines


def site_text(d: dict) -> str:
    s, api = d.get("site", {}), d.get("api", {})
    L = ["=" * W, f"NSO DIAGNOSTICS  -  {d['country']}  -  {d['nso']}",
         f"Checked {d['checked_at']}  |  Agent 1 (agent1_diagnose.py)  |  took {d.get('duration_s', '?')} s", "=" * W, ""]

    L += ["1. WEBSITE"]
    reach = f"yes (HTTP {s.get('status')})" if s.get("reachable") else f"NO - {s.get('blocked') or 'no response'}"
    english = {"yes": "yes", "in-app": "yes - language switch inside the site (same address)", "no": "no English version"}.get(
        d.get("english_config", ""), d.get("english_config") or "unknown")
    L += [f"   Address checked : {d['url']}", f"   Reachable       : {reach}", f"   English version : {english}"]
    if s.get("english_url") and s.get("english_url") != d["url"]:
        L += [f"   English page    : {s['english_url']}"]
    if s.get("platforms"):
        L += [f"   Built with      : {', '.join(s['platforms'])}"]
    if s.get("insecure_tls"):
        L += ["   Security        : the site's TLS certificate is invalid (checked without certificate validation)"]
    for n in (s.get("notes") or []) + ([d["config_notes"]] if d.get("config_notes") else []):
        L += _wrap(f"Note: {n}", 3)
    L += [""]

    L += ["2. API"]
    L += [f"   Verdict         : {api.get('status', 'unknown').upper()} - {ACCESS.get(api.get('python_access'), '')}"]
    L += _wrap(api.get("summary", ""), 3)
    if api.get("docs"):
        L += ["   Documentation   :"] + [f"     {u}" for u in api["docs"]]
    elif api.get("status") != "documented":
        L += ["   Documentation   : none official"]
    if api.get("doc_links"):
        L += ["   Site links mentioning API / open data / databank:"]
        L += [f"     {x['text'] or '(link)'} -> {x['url']}" for x in api["doc_links"][:8]]
    how = api.get("how_to") or {}
    if how:
        L += ["", "   How to call it - curl:"] + _wrap(how.get("curl", ""), 7)
        L += ["", "   How to call it - Python:"] + [f"       {line}" for line in how.get("python", "").splitlines()]
    eps = api.get("endpoints") or []
    if eps:
        ok = sum(1 for e in eps if e.get("works_from_python"))
        L += ["", f"   API sniffing - data calls the website made: {len(eps)} distinct, {ok} worked again from plain Python"]
        for e in eps[:15]:
            flag = "OK " if e.get("works_from_python") else ("n/t" if "works_from_python" not in e else "NO ")
            L += _wrap(f"[{flag}] {e['method']} {e['template']}  ({(e.get('content_type') or '').split(';')[0]})", 5)
    js = api.get("js_findings") or {}
    if js.get("base_urls") or js.get("endpoint_paths"):
        L += ["", "   Found in the site's JavaScript files:"]
        if js.get("base_urls"):
            L += _wrap("API base addresses: " + ", ".join(js["base_urls"][:8]), 5)
        if js.get("endpoint_paths"):
            L += _wrap(f"{len(js['endpoint_paths'])} endpoint paths, e.g. " + ", ".join(js["endpoint_paths"][:10]), 5)
    L += [""]

    n = 3
    for tid, th in d.get("themes", {}).items():
        items = th.get("items") or []
        kinds = {}
        for it in items:  # an "indicator series" item stands for `count` time series
            weight = (it.get("count") or 1) if it["kind"] == "indicator series" else 1
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + weight
        summary = ", ".join(f"{v:,} {k}{'s' if v != 1 and not k.endswith('s') else ''}" for k, v in kinds.items()) or "nothing found"
        L += [f"{n}. {th['label'].upper()}  ({summary})"]
        order = {"publication": 0, "table": 0, "dataset": 0, "indicator series": 1, "survey (microdata)": 1, "file": 2, "page": 3}
        for it in sorted(items, key=lambda x: (order.get(x["kind"], 4), x.get("title", "")))[:MAX_LIST]:
            L += _item_line(it)
        if len(items) > MAX_LIST:
            L += [f"     ... and {len(items) - MAX_LIST} more (full list in {d['id']}.json)"]
        samples = [x for x in d.get("indicator_samples", []) if tid in x.get("themes", [])]
        if samples:
            L += ["   Example indicator time series:"]
            for x in samples[:8]:
                L += _wrap(f"- {x['title']} | {x.get('frequency', '')} | {x.get('coverage', '')} | unit: {x.get('unit') or '?'}", 5)
        L += [""]
        n += 1

    f = d.get("files", {})
    L += [f"{n}. DOWNLOADABLE FILES LINKED FROM THE PAGES CRAWLED"]
    L += [f"   {f.get('pages_crawled', 0)} pages crawled ({f.get('pages_rendered', 0)} rendered in a browser); "
          f"{f.get('total', 0)} file links"]
    if f.get("formats"):
        L += ["   Formats: " + ", ".join(f"{k} {v}" for k, v in f["formats"].items())]
    L += [""]
    n += 1

    L += [f"{n}. SUGGESTED AGENT 2 CONFIGURATION  (copy into config/agent2_sources.yaml under 'sources:')"]
    if d.get("suggested_sources"):
        block = yaml.safe_dump(d["suggested_sources"], allow_unicode=True, sort_keys=False, width=W,
                               default_flow_style=None)  # short lists on one line: [6, 7, 8]
        L += ["     " + line for line in block.splitlines()]
    else:
        L += ["   none - nothing machine-readable was found automatically; see limitations below"]
    L += [""]
    n += 1

    L += [f"{n}. LIMITATIONS / NEXT STEPS"]
    for lim in d.get("limitations") or ["none noted"]:
        L += _wrap(f"- {lim}", 3)
    if d.get("analyst_findings"):
        L += ["", f"{n + 1}. ANALYST FINDINGS (Claude subagent)"] + [f"   {line}" for line in d["analyst_findings"].splitlines()]
    return "\n".join(L) + "\n"


def write_site(d: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{d['id']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    path = out_dir / f"{d['id']}.txt"
    path.write_text(site_text(d), encoding="utf-8")
    return path


def _all(out_dir: Path) -> list[dict]:
    return sorted((json.loads(p.read_text(encoding="utf-8")) for p in out_dir.glob("*.json")), key=lambda d: d["country"])


def write_summary(out_dir: Path) -> Path:
    diags = _all(out_dir)
    theme_ids = list(diags[0]["themes"]) if diags else []
    head = f"{'ID':4} {'Country':22} {'Reach':6} {'English':8} {'API':18} {'Python':8} " + " ".join(f"{t[:10]:>10}" for t in theme_ids) + "  Formats"
    lines = ["NSO DIAGNOSTICS - SUMMARY (one line per NSO; details in <ID>.txt)", "=" * len(head), head, "-" * len(head)]
    for d in diags:
        api, s = d.get("api", {}), d.get("site", {})
        counts = []
        for t in theme_ids:
            items = d["themes"][t]["items"]
            counts.append(sum((it.get("count") or 1) if it["kind"] == "indicator series" else 1 for it in items))
        fmts = ", ".join(list((d.get("files") or {}).get("formats", {}))[:4])
        lines.append(f"{d['id']:4} {d['country'][:22]:22} {'yes' if s.get('reachable') else 'NO':6} "
                     f"{(d.get('english_config') or '?')[:8]:8} {api.get('status', '?')[:18]:18} {api.get('python_access', '?')[:8]:8} "
                     + " ".join(f"{c:>10,}" for c in counts) + f"  {fmts}")
    lines += ["", "Columns per theme = datasets / publications / tables / files / time series found."]
    path = out_dir / "SUMMARY.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_suggestions(out_dir: Path) -> Path:
    header = ("# Suggested Agent 2 sources, generated by Agent 1 from every <ID>.json in this folder.\n"
              "# Review, then copy the entries you want into config/agent2_sources.yaml (under 'sources:').\n"
              "# enabled: false = Agent 1 was not sure this source works well; test it with\n"
              "#   python agent2_monitor.py --nso <ID> --dry-run\n\n")
    entries = [s for d in _all(out_dir) for s in d.get("suggested_sources") or []]
    path = out_dir / "suggested_agent2_sources.yaml"
    path.write_text(header + yaml.safe_dump({"sources": entries}, allow_unicode=True, sort_keys=False, width=110,
                                            default_flow_style=None), encoding="utf-8")
    return path
