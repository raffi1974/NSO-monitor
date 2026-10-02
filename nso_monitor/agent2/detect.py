"""Step 2 of Agent 2 - compare today's listing with what the catalogue already holds.

    new        an item id never seen before (or one that had been removed and is back)
    updated    same item id, different version stamp (new edition, new release date, file changed...)
    unchanged  same id, same version -> nothing to fetch
    removed    known before, missing today

Safety guard: if today's listing is far smaller than before (site half-down, layout changed), nothing is
marked removed - `suspicious` is set instead, and the run log says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListedItem:
    """One entry of a source's catalogue as a connector sees it today."""
    item_id: str
    title: str
    version: str = ""                 # changes whenever the data changes (release date, 'updated' stamp, ETag...)
    kind: str = "dataset"             # edition | indicator | table | dataset | file | survey
    url: str = ""                     # page for humans
    data_url: str = ""                # what fetch() downloads
    format: str = ""
    category: str = ""
    frequency: str = ""
    coverage: str = ""
    fetchable: bool = True            # False = catalogue only (PDFs, microdata behind registration...)
    extra: dict = field(default_factory=dict)


@dataclass
class Changes:
    new: list[ListedItem] = field(default_factory=list)
    updated: list[ListedItem] = field(default_factory=list)
    unchanged: list[ListedItem] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    titles: dict[str, str] = field(default_factory=dict)   # titles of removed items, for the log
    suspicious: bool = False

    @property
    def any(self) -> bool:
        return bool(self.new or self.updated or self.removed)


def compare(listed: list[ListedItem], known: dict[str, dict], min_ratio: float = 0.5) -> Changes:
    ch = Changes()
    seen = set()
    for it in listed:
        if it.item_id in seen:
            continue
        seen.add(it.item_id)
        before = known.get(it.item_id)
        if before is None or before["status"] == "removed":
            ch.new.append(it)
        elif (before.get("version") or "") != (it.version or ""):
            ch.updated.append(it)          # also how an item stuck in 'error' gets retried: when it changes
        else:
            ch.unchanged.append(it)
    active = {k: v for k, v in known.items() if v["status"] != "removed"}
    if active and len(seen) < min_ratio * len(active):
        ch.suspicious = True
        return ch
    for item_id, row in active.items():
        if item_id not in seen:
            ch.removed.append(item_id)
            ch.titles[item_id] = row.get("title", "")
    return ch
