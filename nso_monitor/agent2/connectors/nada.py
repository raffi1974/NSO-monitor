"""NADA microdata catalogues (documented API - e.g. Comoros https://www.nada.inseed-comores.org).

    - nso: COM
      theme: social_statistics
      connector: nada
      options:
        catalog: https://www.nada.inseed-comores.org
        match_theme: true

Catalogue only: new or updated surveys are recorded (title, years, link), but microdata files are not
downloaded - NADA catalogues usually require registration and a request form for them.
"""
from __future__ import annotations

from ...common.text import clean_text, coverage
from .base import Connector, ListedItem, Payload, register


@register
class NadaConnector(Connector):
    name = "nada"

    def list_items(self) -> list[ListedItem]:
        base = self.options["catalog"].rstrip("/")
        match = self.options.get("match_theme", True)
        items, page = [], 1
        while page <= 50:
            data = self.client.get_json(f"{base}/index.php/api/catalog/search", params={"ps": 100, "page": page},
                                        check_robots=False)
            rows = (data.get("result") or {}).get("rows") or []
            for row in rows:
                title = clean_text(row.get("title"))
                if match and not self.in_theme(title):
                    continue
                years = sorted({int(y) for y in (row.get("year_start"), row.get("year_end")) if str(y or "").isdigit() and int(y) > 0})
                items.append(ListedItem(item_id=f"survey:{row.get('id')}", kind="survey", title=title,
                                        version=str(row.get("changed") or row.get("created") or ""),
                                        url=f"{base}/index.php/catalog/{row.get('id')}", format="microdata",
                                        category=row.get("type", "survey"), coverage=coverage(years), fetchable=False))
            if len(rows) < 100:
                break
            page += 1
        return items

    def fetch(self, item: dict) -> Payload:  # pragma: no cover - items are catalogue-only
        return Payload()
