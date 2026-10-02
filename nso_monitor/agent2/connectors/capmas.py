"""Egypt - CAPMAS public JSON API (https://www.capmas.gov.eg:8080, found by Agent 1's API sniffing).

config/agent2_sources.yaml:
    - nso: EGY
      theme: social_statistics
      connector: capmas
      options:
        subject_ids: [20, 21, 22, 23, 24, 25, 26, 27, 28, 55, 56]   # CAPMAS sub-subjects in this theme
        publications: true    # bulletin / survey editions (Excel files are downloaded, PDFs kept as links)
        indicators: true      # indicator time series (values as JSON)

Items and their version stamps (what makes an item "updated"):
    ed:<id>    one publication edition            version = its publish date
    ind:<id>   one indicator time series          version = latest publish date of the publication it belongs to
               -> a new edition of "Labour Force Survey" re-fetches exactly that survey's indicators.
The daily CHECK costs ~2 listing calls per sub-subject; no per-indicator calls unless something changed.
"""
from __future__ import annotations

from ...common.text import period_label
from .base import Connector, ListedItem, Payload, coverage_of, register

API = "https://www.capmas.gov.eg:8080"
SITE = "https://www.capmas.gov.eg"
FREQ = {"Annually": "Annual", "Semi-Annually": "Semi-annual", "Bi-Annually": "Biennial", "5 Years": "Every 5 years",
        "10 Years": "Every 10 years", "Non-periodical": "Irregular"}


def _en(translations, key):
    for tr in translations or []:
        if (tr or {}).get("locale") == "en":
            return (tr.get(key) or "").strip()
    return ""


@register
class CapmasConnector(Connector):
    name = "capmas"

    def _get(self, path, **params):
        return self.client.get_json(f"{API}/{path}", params=params or None, check_robots=False,
                                    headers={"locale": "en", "Accept": "application/json"})

    def list_items(self) -> list[ListedItem]:
        subject_ids = [int(s) for s in self.options.get("subject_ids") or []]
        if not subject_ids:
            raise ValueError("capmas connector needs options.subject_ids (see Agent 1's EGY report)")
        titles, mains = {}, {}
        for main in self._get("api/Subject")["data"]:
            for sub in main.get("subSubjects") or []:
                titles[sub["id"]] = _en(sub.get("subjectTranslations"), "title") or sub.get("title", "")
                mains[sub["id"]] = main["id"]
        items, latest = [], {}

        if self.options.get("publications", True):
            for sid in subject_ids:
                page = 1
                while True:
                    r = self._get("api/Publication/Search", SubjectId=sid, PageSize=100, CurrentPage=page)
                    for row in r.get("data") or []:
                        d = row.get("publicationDetail") or {}
                        stamp = (d.get("publishDate") or d.get("releaseDate") or "")[:19]
                        latest[row["id"]] = max(latest.get(row["id"], ""), stamp)
                        name = _en(row.get("publicationTranslations"), "name") or row.get("name", "")
                        per = row.get("periodic") or {}
                        freq = _en(per.get("periodicTranslations"), "name") or per.get("name", "")
                        period = period_label(d.get("year"), d.get("quarter"), d.get("month"))
                        excel = d.get("excelUrl")
                        items.append(ListedItem(
                            item_id=f"ed:{d.get('id')}", kind="edition", title=f"{name.strip()} - {period}".strip(" -"),
                            version=stamp, url=f"{SITE}/publications/{row['id']}",
                            data_url=API + excel if excel else (API + d["pdfUrl"] if d.get("pdfUrl") else ""),
                            format="XLSX" if excel else ("PDF" if d.get("pdfUrl") else ""), category=titles.get(sid, ""),
                            frequency=FREQ.get(freq, freq), coverage=period, fetchable=bool(excel) and self.wants("XLSX"),
                            extra={"publication_id": row["id"], "pdf": API + d["pdfUrl"] if d.get("pdfUrl") else ""}))
                    if page >= int((r.get("page") or {}).get("totalPages") or 1):
                        break
                    page += 1

        if self.options.get("indicators", True):
            seen = set()
            for sid in subject_ids:
                data = (self._get(f"api/Subject/SubSubjectWithIndicator/{sid}").get("data") or {})
                for pub in data.get("publicationWithIndicators") or []:
                    for ind in pub.get("indicators") or []:
                        iid = ind.get("indicatorId")
                        if iid is None or iid in seen:
                            continue
                        seen.add(iid)
                        items.append(ListedItem(
                            item_id=f"ind:{iid}", kind="indicator",
                            title=_en(ind.get("indicatorTranslations"), "name") or ind.get("name", ""),
                            version=latest.get(pub.get("id"), "listed"),
                            url=f"{SITE}/data/mainSubject/{mains.get(sid)}/subSubject/{sid}/data-visualization/{iid}",
                            data_url=f"{API}/api/Indicator/IndicatorFilter?IndicatorId={iid}&SubSubjectId={sid}",
                            format="JSON", category=titles.get(sid, ""), fetchable=True,
                            extra={"publication_id": pub.get("id"),
                                   "publication": _en(pub.get("publicationTranslations"), "name") or pub.get("name", "")}))
        return items

    def fetch(self, item: dict) -> Payload:
        if item["kind"] == "edition":
            return self.file_payload(item["data_url"], item["format"])
        rows = self.client.get_json(item["data_url"], check_robots=False, headers={"locale": "en"}).get("data") or []
        obs = []
        for row in rows:
            for o in row.get("data") or []:
                obs.append({"series": item["title"], "breakdown": (row.get("name") or "").strip(),
                            "period": period_label(o.get("year"), o.get("quarter"), o.get("month")), "value": o.get("value")})
        return Payload(observations=obs, coverage=coverage_of([o["period"] for o in obs]))
