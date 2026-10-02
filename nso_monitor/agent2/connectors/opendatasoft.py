"""Opendatasoft / Huwise open-data portals (documented API - e.g. Bahrain www.data.gov.bh, Qatar www.data.gov.qa).

    - nso: BHR
      theme: economic_statistics
      connector: opendatasoft
      options:
        portal: https://www.data.gov.bh
        match_theme: true      # keep only datasets whose title / portal theme / keywords match the theme

Items: one per dataset ("ds:<dataset_id>"), version = the portal's "data_processed"/"modified" stamp.
Fetch: the whole dataset as CSV (Explore API v2.1 export), stored row by row.
"""
from __future__ import annotations

from .base import Connector, ListedItem, Payload, register


@register
class OpendatasoftConnector(Connector):
    name = "opendatasoft"

    def list_items(self) -> list[ListedItem]:
        portal = self.options["portal"].rstrip("/")
        api = f"{portal}/api/explore/v2.1/catalog/datasets"
        match = self.options.get("match_theme", True)
        items, offset, total = [], 0, 1
        while offset < min(total, int(self.options.get("max_datasets", 3000))):
            data = self.client.get_json(api, params={"limit": 100, "offset": offset}, check_robots=False)
            total = data.get("total_count", 0)
            for row in data.get("results") or []:
                meta = (row.get("metas") or {}).get("default") or {}
                title = meta.get("title") or row["dataset_id"]
                tags = " ".join((meta.get("theme") or []) + (meta.get("keyword") or []))
                if match and not self.in_theme(title, tags):
                    continue
                items.append(ListedItem(
                    item_id=f"ds:{row['dataset_id']}", kind="dataset", title=title,
                    version=meta.get("data_processed") or meta.get("modified") or "",
                    url=f"{portal}/explore/dataset/{row['dataset_id']}/",
                    data_url=f"{api}/{row['dataset_id']}/exports/csv", format="CSV",
                    category=", ".join(meta.get("theme") or []), fetchable=self.wants("CSV")))
            offset += 100
        return items

    def fetch(self, item: dict) -> Payload:
        return self.file_payload(item["data_url"], "CSV")
