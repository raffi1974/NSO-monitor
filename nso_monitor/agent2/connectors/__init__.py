"""Connectors - one module per KIND of website. The `connector:` field of each source in
config/agent2_sources.yaml picks one of these by name:

    capmas        Egypt's public JSON API
    cas_cpi       Lebanon CAS's undocumented CPI dashboard + ICP table REST routes (wp-json/cas-cpi, cas/v1)
    pxweb         PX-Web databanks (Jordan)
    opendatasoft  Opendatasoft / Huwise open-data portals (Bahrain, Qatar)
    nada          NADA microdata catalogues (Comoros) - catalogue only
    json_api      any JSON API, described in YAML only (for APIs found by Agent 1's sniffing)
    scrape        any website without an API: tracks the files linked from given pages

To support a new kind of website: copy scrape.py or json_api.py, give the class a new `name`, and import
the module below.
"""
from .base import CONNECTORS, Connector, Payload, register  # noqa: F401
from . import capmas, cas_cpi, json_api, nada, opendatasoft, pxweb, scrape  # noqa: F401,E402  (registers them)
