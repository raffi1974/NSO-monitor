---
description: Deep-dive diagnosis of one NSO website with the Claude subagent (after running agent1_diagnose.py)
argument-hint: <ID>   (e.g. /diagnose JOR)
---

Invoke the `nso-diagnostics` subagent for site id `$1`.

If `$1` is empty, ask which site id from `config/agent1_sites.yaml` to diagnose before doing anything else.
