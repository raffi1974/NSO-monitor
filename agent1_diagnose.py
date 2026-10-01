"""AGENT 1 - diagnose NSO websites (run on demand, never scheduled).

    python agent1_diagnose.py                 all sites in config/agent1_sites.yaml
    python agent1_diagnose.py --nso JOR TUN   only these sites

Results in output/agent1/: <ID>.txt (read this), <ID>.json, SUMMARY.txt, suggested_agent2_sources.yaml.
For a deeper look at a difficult site, ask Claude Code: /diagnose <ID>
"""
import argparse
import logging
import sys

from nso_monitor.agent1.run import run
from nso_monitor.common.config import ConfigError


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Arabic titles on a Windows console
    parser = argparse.ArgumentParser(description="Agent 1: diagnose NSO websites")
    parser.add_argument("--nso", nargs="+", metavar="ID", help="site ids from config/agent1_sites.yaml (default: all)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every request")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        results = run(args.nso)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\nDone. Reports in output/agent1/:")
    for d in results:
        api = d.get("api", {})
        print(f"  {d['id']}.txt  reachable={d.get('site', {}).get('reachable')}  api={api.get('status')}  "
              f"python={api.get('python_access')}  " +
              "  ".join(f"{tid}={len(th['items'])}" for tid, th in d["themes"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
