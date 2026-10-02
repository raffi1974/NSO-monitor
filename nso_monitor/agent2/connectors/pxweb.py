"""PX-Web databanks (documented API - e.g. Jordan's JorInfo, https://jorinfo.dos.gov.jo/Databank/pxweb/en/).

    - nso: JOR
      theme: social_statistics
      connector: pxweb
      options:
        api_root: https://jorinfo.dos.gov.jo/Databank/api/v1/en/
        databases: [ ... ]      # optional: only these databases (Agent 1's report lists them)
        match_theme: true       # keep only tables whose title / folder matches the theme keywords

Items: one per table ("tbl:<path>"), version = the table's "updated" timestamp from PX-Web.
Fetch: the whole table as JSON-stat2, turned into tidy observations (breakdown | period | value).
"""
from __future__ import annotations

import itertools

import logging

from .base import Connector, ListedItem, Payload, coverage_of, register

log = logging.getLogger(__name__)

TIME_NAMES = {"year", "years", "time", "tid", "period", "date", "month", "quarter", "annee", "السنه", "السنة"}


def jsonstat2_observations(ds: dict, series: str) -> list[dict]:
    ids, sizes = ds["id"], ds["size"]
    values = ds.get("value") or []
    time_ids = (ds.get("role") or {}).get("time") or [d for d in ids if d.lower() in TIME_NAMES] or [ids[-1]]
    cats = []
    for dim in ids:
        cat = ds["dimension"][dim]["category"]
        index = cat.get("index")
        codes = (sorted(index, key=index.get) if isinstance(index, dict) else index) or list(cat.get("label", {}))
        labels = cat.get("label", {})
        cats.append([labels.get(c, c) for c in codes])
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    out = []
    for combo in itertools.product(*(range(s) for s in sizes)):
        flat = sum(i * s for i, s in zip(combo, strides))
        v = values[flat] if isinstance(values, list) else values.get(str(flat))
        if v is None:
            continue
        labels = [cats[d][i] for d, i in enumerate(combo)]
        out.append({"series": series, "value": v,
                    "period": " ".join(lbl for d, lbl in zip(ids, labels) if d in time_ids),
                    "breakdown": " | ".join(lbl for d, lbl in zip(ids, labels) if d not in time_ids)})
    return out


@register
class PxWebConnector(Connector):
    name = "pxweb"

    def list_items(self) -> list[ListedItem]:
        root = self.options["api_root"].rstrip("/") + "/"
        dbs = self.options.get("databases") or [d.get("dbid") or d.get("id") for d in self.client.get_json(root, check_robots=False)]
        max_tables = int(self.options.get("max_tables", 3000))
        match = self.options.get("match_theme", True)
        items = []

        def walk(path, crumbs, depth):
            try:  # PX-Web trees are occasionally irregular (a node typed as a folder that 404s); one bad
                nodes = self.client.get_json(root + path, check_robots=False, retries=1, timeout=20)  # branch
            except Exception as exc:  # noqa: BLE001 - must not lose every table found in the others
                log.warning("pxweb: skipping %s: %s", root + path, exc)
                return
            for node in nodes:
                if len(items) >= max_tables:
                    return
                if node.get("type") == "l" and depth < 6:
                    walk(path + node["id"] + "/", crumbs + [node.get("text", "")], depth + 1)
                elif node.get("type") == "t":
                    title = node.get("text", "")
                    if match and not self.in_theme(title, " ".join(crumbs)):
                        continue
                    items.append(ListedItem(item_id=f"tbl:{path}{node['id']}", kind="table", title=title,
                                            version=node.get("updated", ""), url=root + path + node["id"],
                                            data_url=root + path + node["id"], format="JSON-stat",
                                            category=" / ".join(crumbs), fetchable=True))

        for db in dbs:
            walk(db + "/", [db], 1)
        return items

    def fetch(self, item: dict) -> Payload:
        # PX-Web has no "give me everything" shortcut: an empty query selects nothing for every
        # variable, which some tables answer with a degenerate response (missing dimensions) rather
        # than the full table - found live against Jordan's JorInfo (an empty query raised IndexError
        # downstream). The documented, standard way is to GET the table's metadata (its variables and
        # every value each one can take) and explicitly select ALL values of EVERY variable; leaving a
        # variable out of the query eliminates (sums over) it instead of returning it disaggregated.
        meta = self.client.get_json(item["data_url"], check_robots=False)
        query = [{"code": v["code"], "selection": {"filter": "item", "values": v.get("values") or []}}
                for v in meta.get("variables", [])]
        r = self.client.request("POST", item["data_url"], json={"query": query, "response": {"format": "json-stat2"}},
                                check_robots=False)
        r.raise_for_status()
        obs = jsonstat2_observations(r.json(), item["title"])
        return Payload(observations=obs, coverage=coverage_of([o["period"] for o in obs]))
