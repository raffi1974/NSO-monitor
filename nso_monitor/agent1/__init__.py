"""Agent 1 - website diagnostics (run on demand with agent1_diagnose.py).

Steps, one module each, in the order run.py calls them:
    site.py         is the site up? English version? blocked? which platform (WordPress, SharePoint, SPA...)?
    api_probe.py    is there a DOCUMENTED API (SDMX, PX-Web, CKAN, Opendatasoft, NADA, Swagger...)?
    sniff.py        API SNIFFING: which hidden data calls does the site make, and do they work from Python?
    crawl.py        which social / economic datasets, time series and downloadable files are on the pages?
    recognizers.py  for known platforms (CAPMAS, PX-Web, Opendatasoft, NADA) list the catalogue via the API
    report.py       write output/agent1/<ID>.txt, <ID>.json, SUMMARY.txt, suggested_agent2_sources.yaml
    run.py          runs the steps above for each site
"""
