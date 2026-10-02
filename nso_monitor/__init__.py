"""NSO monitor - two separate programs sharing one engine.

Agent 1 (agent1_diagnose.py)  : on-demand diagnostics of NSO websites -> output/agent1/*.txt
Agent 2 (agent2_monitor.py)   : daily monitor, fetches only new data -> data/<theme>/*.sqlite
Dashboard (build_dashboard.py): dashboard/index.html from both agents' results

Sub-packages:
    common/     code both agents need (config files, polite HTTP client, browser, keyword matching)
    agent1/     Agent 1's steps (site check, API probe, sniffing, crawl, recognisers, report)
    agent2/     Agent 2's steps (change detection, SQLite stores, connectors per website type)
    dashboard/  the dashboard builder and its HTML template
"""

__version__ = "2.0.0"
