"""Builds the offline dashboard (dashboard/index.html) from both agents' output:

    output/agent1/<ID>.json     the one-off diagnosis: API status, docs, a snapshot of datasets found
    data/<theme>/catalogue.sqlite   Agent 2's live monitoring state (when a source has been enabled)

A site appears on the dashboard as soon as Agent 1 has diagnosed it; once Agent 2 monitors it too, the
dashboard prefers Agent 2's live, current catalogue over Agent 1's one-time snapshot for that theme.
"""
