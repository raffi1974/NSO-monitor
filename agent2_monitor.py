"""AGENT 2 - the daily monitor (scheduled on GitHub by .github/workflows/agent2-daily.yml; can also run locally).

    python agent2_monitor.py                                  every enabled source in config/agent2_sources.yaml
    python agent2_monitor.py --nso EGY --theme social_statistics
    python agent2_monitor.py --nso JOR --dry-run              check + compare only, write nothing (test a new source)
    python agent2_monitor.py --max-fetch 200                  fetch at most 200 items this run

It fetches ONLY what is new or changed since the last run, and stores it in
data/<theme>/catalogue.sqlite (what exists) and data/<theme>/datasets.sqlite (the values).
"""
import argparse
import logging
import sys

from nso_monitor.agent2.runner import run
from nso_monitor.common.config import ConfigError


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Agent 2: daily monitor - fetch only new data")
    parser.add_argument("--nso", nargs="+", metavar="ID", help="only these NSOs (runs them even if enabled: false)")
    parser.add_argument("--theme", nargs="+", metavar="THEME", help="only these themes")
    parser.add_argument("--dry-run", action="store_true", help="check and compare only; write nothing")
    parser.add_argument("--max-fetch", type=int, help="override max_fetch_per_run")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        s = run(args.nso, args.theme, args.dry_run, args.max_fetch)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"\n{'DRY RUN - nothing written. ' if s['dry_run'] else ''}"
          f"new={s['new']} updated={s['updated']} removed={s['removed']} fetched={s['fetched']} "
          f"fetch_errors={s['fetch_errors']} still_waiting={s['backlog']}")
    if not s.get("data_changed") and not s["dry_run"]:
        print("No new data today - nothing was downloaded.")
    failed = [x for x in s["sources"] if x["status"] == "failed"]
    for x in failed:
        print(f"  FAILED {x['source']}: {x['error']}")
    return 2 if failed and len(failed) == len(s["sources"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
