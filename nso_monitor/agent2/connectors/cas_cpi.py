"""Lebanon - CAS's own CPI dashboard API: undocumented WordPress REST routes that are NOT part of the
generic wp/v2 content API. Found via the site's own REST discovery document, which lists every namespace
a WordPress site has registered:

    curl https://www.cas.gov.lb/wp-json/ | python -m json.tool   # -> "namespaces": [..., "cas-cpi/v2", "cas/v1", ...]

Two real statistics APIs live under these namespaces (both plain JSON, no auth, no cookies needed):
  - cas-cpi/v2  the Consumer Price Index dashboard: monthly division-level CPI for Lebanon and 6
    governorates back to 2014, plus a ~100-row subclass breakdown and two supplementary indices
    (Education, Fuel) for the latest month.
  - cas/v1/icp  a small International Comparison Program table (PPP / exchange rate / price level
    index for Lebanon vs. ~15 other countries), refreshed only when a new ICP round is published.

config/agent2_sources.yaml:
    - nso: LBN
      theme: economic_statistics
      method: api
      connector: cas_cpi
      options:
        geographies: [lebanon, beirut, mount_lebanon, north, bekaa, south, nabatieh]  # optional, default: all
        subclass: true          # also track the subclass-index snapshot (latest published month only)
        supplementary: true     # also track the Education / Fuel sub-indices (latest published month only)
        icp: true                # also track the ICP price-level comparison table

Items and their version stamps:
    cpi:<geo>                one division-level CPI series for a geography, full 2014-> history in one call
                              (tab=inflation keeps every division/month pair, not just the chart's total row)
                              version = bootstrap's dataset_version + latest published year-month
    cpi:subclass              ~100 subclass indexes, Lebanon only. Each call returns exactly 3 reference
                              months (latest, latest-1, and the same month last year - what the dashboard
                              needs for its month/year change figures), NOT the full 2014-> history like the
                              geography series above; earlier months would need one call each - left for a
                              future enhancement if that detail is needed
    cpi:supplementary:<key>   Education / Fuel price sub-index, Lebanon only, same 3-reference-month window
    icp                        the ICP comparison table for its current round; version = round year + a hash
                              of the rows (changes when CAS publishes the next round)
"""
from __future__ import annotations

import hashlib
import json

from .base import Connector, ListedItem, Payload, coverage_of, register

CPI_API = "https://www.cas.gov.lb/wp-json/cas-cpi/v2"
ICP_API = "https://www.cas.gov.lb/wp-json/cas/v1/icp"
PAGE = "https://www.cas.gov.lb/economic-statistics/cpi/"


@register
class CasCpiConnector(Connector):
    name = "cas_cpi"

    def list_items(self) -> list[ListedItem]:
        boot = self.client.get_json(f"{CPI_API}/bootstrap", check_robots=False)
        dver = boot.get("dataset_version", "")
        meta = boot.get("meta") or {}
        year, month = meta.get("latest_year"), meta.get("latest_month")
        latest = f"{year}-{int(month):02d}" if year and month else ""
        stamp = f"{dver}:{latest}"
        labels = {g["code"]: g.get("label_en") or g["code"] for g in boot.get("geographies") or []}
        geos = self.options.get("geographies") or list(labels) or ["lebanon"]

        items = [ListedItem(item_id=f"cpi:{geo}", kind="indicator", title=f"CPI - {labels.get(geo, geo)}",
                            version=stamp, url=PAGE, data_url=f"{CPI_API}/view?tab=inflation&geo={geo}",
                            format="JSON", category="Consumer Price Index", frequency="Monthly",
                            coverage=latest, fetchable=True)
                 for geo in geos]

        if self.options.get("subclass", True) and year and month:
            items.append(ListedItem(
                item_id="cpi:subclass", kind="indicator", title="CPI subclass indexes - Lebanon", version=stamp,
                url=PAGE, data_url=f"{CPI_API}/view?tab=subclass&year={year}&month={month}&export=1", format="JSON",
                category="Consumer Price Index", frequency="Monthly", coverage=latest, fetchable=True))

        if self.options.get("supplementary", True) and year and month:
            for fam in boot.get("supplementary_families") or []:
                key = fam.get("key")
                items.append(ListedItem(
                    item_id=f"cpi:supplementary:{key}", kind="indicator",
                    title=f"{fam.get('label_en', key)} - Lebanon", version=stamp, url=PAGE,
                    data_url=f"{CPI_API}/view?tab=supplementary&family={key}&year={year}&month={month}&export=1",
                    format="JSON", category="Consumer Price Index", frequency="Monthly", coverage=latest,
                    fetchable=True))

        if self.options.get("icp", True):
            icp = self.client.get_json(ICP_API, check_robots=False)
            rows = icp.get("rows") or []
            if rows:
                digest = hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:12]
                items.append(ListedItem(
                    item_id="icp", kind="table", title=f"ICP price level index (PPP) vs. other countries - {icp.get('year')}",
                    version=f"{icp.get('year')}:{digest}", url=PAGE, data_url=ICP_API, format="JSON",
                    category="International Comparison Program", frequency="Irregular (ICP round)",
                    coverage=str(icp.get("year") or ""), fetchable=True))
        return items

    def fetch(self, item: dict) -> Payload:
        if item["item_id"] == "icp":
            payload = self.client.get_json(item["data_url"], check_robots=False)
            year = str(payload.get("year") or "")
            obs = [{"series": f"ICP {metric}", "breakdown": row.get("country", ""), "period": year, "value": val,
                    "unit": metric}
                   for row in payload.get("rows") or [] for metric, val in row.items() if metric != "country"]
            return Payload(observations=obs, coverage=year)

        payload = self.client.get_json(item["data_url"], check_robots=False)
        obs = []
        for year, months in (payload.get("data") or {}).items():
            for month, tabs in (months or {}).items():
                for by_geo in (tabs or {}).values():
                    for geo, table in (by_geo or {}).items():
                        for row in (table or {}).get("rows") or []:
                            value = (row.get("values") or {}).get("current_index")
                            if value is None:
                                continue
                            obs.append({"series": row.get("label_en") or row.get("code") or "", "breakdown": geo,
                                        "period": f"{year}-{int(month):02d}", "value": value, "unit": "index"})
        return Payload(observations=obs, coverage=coverage_of([o["period"] for o in obs]))
