"""Rebuild dashboard/index.html from output/agent1/*.json and every data/<theme>/catalogue.sqlite.

    python build_dashboard.py

Safe to run any time, as often as you like - it only reads, never writes to config/ or data/.
"""
import sys

from nso_monitor.dashboard.build import build_dashboard


def main() -> int:
    path = build_dashboard()
    print(f"Dashboard rebuilt: {path}\nOpen it directly in a browser - no server needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
