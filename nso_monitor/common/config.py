"""Loads and checks the three YAML files in config/.

    config/themes.yaml          shared: themes and their keywords
    config/agent1_sites.yaml    Agent 1: which websites to diagnose
    config/agent2_sources.yaml  Agent 2: what to monitor every day

Every problem found is collected and reported at once (ConfigError.errors), so a typo in one entry
never hides a typo in another.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

AGENT1_DEFAULTS = {"max_pages": 40, "max_depth": 2, "sniff_pages": 4, "request_delay_seconds": 0.5,
                   "browser_channel": "msedge", "timeout_seconds": 40, "max_parallel_sites": 5}
AGENT2_DEFAULTS = {"max_fetch_per_run": 1500, "request_delay_seconds": 0.5, "max_concurrency": 2,
                   "download_formats": ["xlsx", "xls", "csv", "json"], "max_file_mb": 25,
                   "browser_channel": "msedge", "timeout_seconds": 60}

THEME_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
SITE_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Configuration problems:\n  - " + "\n  - ".join(errors))


def _read(path: Path) -> Any:
    if not path.exists():
        raise ConfigError([f"missing file {path.relative_to(PROJECT_ROOT)}"])
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def browser_channel(settings: dict) -> str | None:
    """The browser Playwright should drive. The environment variable NSO_BROWSER_CHANNEL wins (the GitHub
    workflow sets it to "" = Playwright's own Chromium, because runners have no Edge)."""
    value = os.environ.get("NSO_BROWSER_CHANNEL", settings.get("browser_channel") or "")
    return value or None


# ------------------------------------------------------------------------------------------------
# Themes (shared)
# ------------------------------------------------------------------------------------------------
@dataclass
class Theme:
    id: str
    label: str
    keywords: dict[str, list[str]]
    exclude: dict[str, list[str]] = field(default_factory=dict)


def load_themes(path: Path | None = None) -> dict[str, Theme]:
    raw = _read(path or CONFIG_DIR / "themes.yaml")
    errors, themes = [], {}
    for theme_id, body in (raw or {}).items():
        if theme_id == "not_a_dataset":
            continue  # reserved: a document-type filter, not a theme - see load_non_dataset_keywords()
        body = body or {}
        if not THEME_ID.match(str(theme_id)):
            errors.append(f"themes.yaml: theme id '{theme_id}' must be lower_case_with_underscores")
        keywords = {lang: [str(k) for k in (words or [])] for lang, words in (body.get("keywords") or {}).items()}
        if not any(keywords.values()):
            errors.append(f"themes.yaml: theme '{theme_id}' has no keywords")
        themes[str(theme_id)] = Theme(id=str(theme_id), label=str(body.get("label") or theme_id),
                                      keywords=keywords, exclude=body.get("exclude") or {})
    if not themes:
        errors.append("themes.yaml: no theme defined")
    if errors:
        raise ConfigError(errors)
    return themes


def load_non_dataset_keywords(path: Path | None = None) -> dict[str, list[str]]:
    """themes.yaml's 'not_a_dataset' block: titles matching these are supporting documents
    (questionnaires, manuals, ...), not datasets - Agent 1 leaves them out of its reports."""
    raw = _read(path or CONFIG_DIR / "themes.yaml")
    body = (raw or {}).get("not_a_dataset") or {}
    return {lang: [str(k) for k in (words or [])] for lang, words in (body.get("keywords") or {}).items()}


# ------------------------------------------------------------------------------------------------
# Agent 1: sites
# ------------------------------------------------------------------------------------------------
@dataclass
class Site:
    id: str
    country: str
    nso: str
    url: str
    english: str = ""
    languages: list[str] = field(default_factory=list)
    start_urls: list[str] = field(default_factory=list)
    portals: list[dict] = field(default_factory=list)
    notes: str = ""


@dataclass
class Agent1Config:
    settings: dict
    sites: list[Site]

    def select(self, ids: list[str] | None) -> list[Site]:
        if not ids:
            return list(self.sites)
        wanted = {i.upper() for i in ids}
        unknown = wanted - {s.id for s in self.sites}
        if unknown:
            raise ConfigError([f"unknown site id(s) {sorted(unknown)} - known: {', '.join(s.id for s in self.sites)}"])
        return [s for s in self.sites if s.id in wanted]


def load_agent1(path: Path | None = None) -> Agent1Config:
    raw = _read(path or CONFIG_DIR / "agent1_sites.yaml")
    settings = {**AGENT1_DEFAULTS, **(raw.get("settings") or {})}
    errors, sites, seen = [], [], set()
    known = {f for f in Site.__dataclass_fields__}
    for n, entry in enumerate(raw.get("sites") or [], 1):
        entry = entry or {}
        where = f"agent1_sites.yaml entry {n} ({entry.get('id', '?')})"
        problems = []
        extra = set(entry) - known
        if extra:
            problems.append(f"unknown field(s) {sorted(extra)}")
        for required in ("id", "country", "nso", "url"):
            if not entry.get(required):
                problems.append(f"'{required}' is required")
        sid = str(entry.get("id", "")).upper()
        if sid and not SITE_ID.match(sid):
            problems.append("id must be upper case letters/digits, e.g. JOR")
        if sid and sid in seen:
            problems.append(f"duplicate id {sid}")
        seen.add(sid)
        if entry.get("url") and not re.match(r"^https?://", str(entry["url"])):
            problems.append("url must start with http:// or https://")
        if problems:
            errors += [f"{where}: {p}" for p in problems]
            continue
        data = {k: v for k, v in entry.items() if k in known}
        data.update(id=sid, english=str(data.get("english") or ""), notes=data.get("notes") or "",
                    languages=data.get("languages") or [], start_urls=data.get("start_urls") or [],
                    portals=data.get("portals") or [])
        sites.append(Site(**data))
    if errors:
        raise ConfigError(errors)
    return Agent1Config(settings=settings, sites=sites)


# ------------------------------------------------------------------------------------------------
# Agent 2: sources
# ------------------------------------------------------------------------------------------------
@dataclass
class Source:
    nso: str
    theme: str
    connector: str
    method: str = ""                     # api | scrape (informative)
    enabled: bool = True
    name: str = ""
    options: dict = field(default_factory=dict)
    pages: list[str] = field(default_factory=list)
    render_js: Any = "auto"              # auto | true | false  (scrape connector)
    notes: str = ""
    id: str = ""

    def __post_init__(self):
        self.nso = self.nso.upper()
        if not self.id:
            self.id = f"{self.nso}_{self.theme}_{self.connector}"


@dataclass
class Agent2Config:
    settings: dict
    sources: list[Source]

    def select(self, nso: list[str] | None = None, theme: list[str] | None = None,
               include_disabled: bool = False) -> list[Source]:
        wanted_nso = {n.upper() for n in nso} if nso else None
        out = []
        for s in self.sources:
            if wanted_nso is not None and s.nso not in wanted_nso:
                continue
            if theme and s.theme not in theme:
                continue
            if not s.enabled and not (include_disabled or wanted_nso):
                continue
            out.append(s)
        return out


def load_agent2(path: Path | None = None, themes: dict[str, Theme] | None = None) -> Agent2Config:
    from ..agent2.connectors import CONNECTORS  # local import: connectors import this module

    themes = themes if themes is not None else load_themes()
    raw = _read(path or CONFIG_DIR / "agent2_sources.yaml")
    settings = {**AGENT2_DEFAULTS, **(raw.get("settings") or {})}
    errors, sources, seen = [], [], set()
    known = {f for f in Source.__dataclass_fields__}
    for n, entry in enumerate(raw.get("sources") or [], 1):
        entry = entry or {}
        where = f"agent2_sources.yaml entry {n} ({entry.get('nso', '?')}/{entry.get('theme', '?')})"
        problems = []
        extra = set(entry) - known
        if extra:
            problems.append(f"unknown field(s) {sorted(extra)}")
        for required in ("nso", "theme", "connector"):
            if not entry.get(required):
                problems.append(f"'{required}' is required")
        if entry.get("theme") and entry["theme"] not in themes:
            problems.append(f"theme '{entry['theme']}' is not defined in themes.yaml")
        if entry.get("connector") and entry["connector"] not in CONNECTORS:
            problems.append(f"connector '{entry['connector']}' does not exist "
                            f"(available: {', '.join(sorted(CONNECTORS))})")
        if entry.get("connector") == "scrape" and not entry.get("pages"):
            problems.append("the scrape connector needs at least one page in 'pages'")
        if problems:
            errors += [f"{where}: {p}" for p in problems]
            continue
        src = Source(**{**entry, "options": entry.get("options") or {}, "pages": entry.get("pages") or []})
        if src.id in seen:
            errors.append(f"{where}: duplicate source id {src.id} (add an explicit 'id:' to one of them)")
        seen.add(src.id)
        sources.append(src)
    if errors:
        raise ConfigError(errors)
    return Agent2Config(settings=settings, sources=sources)
