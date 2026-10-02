"""A generic JSON API, described entirely in YAML - for APIs found by Agent 1's sniffing that have no
dedicated connector yet. No Python needed:

    - nso: XYZ
      theme: economic_statistics
      connector: json_api
      options:
        list_url: "https://stats.example.gov/api/datasets?page={page}"   # {page} optional
        max_pages: 5
        items_path: "data.items"        # where the list sits in the JSON ("" = the response itself)
        id_field: "id"
        title_field: "name"
        version_field: "lastUpdated"    # optional; without it any change to the entry counts as an update
        data_url: "https://stats.example.gov/api/datasets/{id}/data"   # optional; what to download per item
        headers: {locale: en}           # optional extra request headers
        match_theme: false              # true = keep only entries whose title matches the theme keywords
"""
from __future__ import annotations

import hashlib
import json

from .base import Connector, ListedItem, Payload, parse_table_file, register


def dig(obj, path: str):
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


@register
class JsonApiConnector(Connector):
    name = "json_api"

    def list_items(self) -> list[ListedItem]:
        o = self.options
        if not o.get("list_url"):
            raise ValueError("json_api connector needs options.list_url")
        headers = o.get("headers") or {}
        rows = []
        for page in range(1, int(o.get("max_pages", 1)) + 1):
            url = o["list_url"].format(page=page) if "{page}" in o["list_url"] else o["list_url"]
            batch = dig(self.client.get_json(url, headers=headers, check_robots=False), o.get("items_path", "")) or []
            rows += batch
            if not batch or "{page}" not in o["list_url"]:
                break
        items = []
        for row in rows:
            iid = str(dig(row, o.get("id_field", "id")))
            title = str(dig(row, o.get("title_field", "title")) or iid)
            if o.get("match_theme") and not self.in_theme(title):
                continue
            version = (str(dig(row, o["version_field"])) if o.get("version_field")
                       else hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:12])
            scalars = {k: v for k, v in row.items() if isinstance(v, (str, int, float))} if isinstance(row, dict) else {}
            data_url = o["data_url"].format(**{**scalars, "id": iid}) if o.get("data_url") else ""
            items.append(ListedItem(item_id=f"json:{iid}", kind="dataset", title=title, version=version,
                                    url=str(dig(row, o["url_field"])) if o.get("url_field") else "",
                                    data_url=data_url, format="JSON", fetchable=bool(data_url)))
        return items

    def fetch(self, item: dict) -> Payload:
        content, meta = self.download(item["data_url"], "JSON", headers=self.options.get("headers") or {})
        return Payload(sheets=parse_table_file(content, "JSON", item["data_url"]), files=[meta])
