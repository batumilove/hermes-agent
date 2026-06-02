#!/usr/bin/env python3
"""Print a compact report for context-efficiency telemetry logs."""

from __future__ import annotations

import argparse

from agent.context_efficiency_report import build_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Hermes context-efficiency telemetry JSONL logs")
    parser.add_argument("path", nargs="?", help="Telemetry JSONL path; defaults to HERMES_HOME/logs/context_efficiency.jsonl")
    parser.add_argument("--limit", type=int, default=None, help="Only read the last N JSONL rows")
    args = parser.parse_args()
    print(build_report(args.path, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
