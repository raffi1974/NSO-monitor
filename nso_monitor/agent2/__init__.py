"""Agent 2 - the daily monitor (agent2_monitor.py; scheduled on GitHub by .github/workflows/agent2-daily.yml).

For every enabled source in config/agent2_sources.yaml:
    1. CHECK  - the source's connector lists what is published now (cheap: catalogue listings only)
    2. COMPARE with what we saw last time (detect.py) -> new / updated / removed items
    3. FETCH  - only for new or updated items (and only up to max_fetch_per_run; the rest waits for tomorrow)
    4. STORE  - data/<theme>/catalogue.sqlite (what exists) and data/<theme>/datasets.sqlite (the values)

Modules:
    runner.py       the steps above
    detect.py       the comparison (step 2)
    store.py        the two SQLite databases per theme
    connectors/     one module per kind of website: capmas, pxweb, opendatasoft, nada, json_api, scrape
"""
