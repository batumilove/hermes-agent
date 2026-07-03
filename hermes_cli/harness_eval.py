"""Implementation of the ``hermes harness`` CLI subcommand."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agent.harness_eval import evaluate_case_files


def cmd_harness(args: argparse.Namespace) -> int:
    """Dispatch ``hermes harness`` subcommands."""
    subcommand = getattr(args, "harness_command", None)
    if subcommand == "eval":
        return _cmd_harness_eval(args)
    # Unknown or missing subcommand — print to stderr and exit with usage error.
    print("harness: unknown subcommand", file=sys.stderr)
    return 2


def _cmd_harness_eval(args: argparse.Namespace) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    paths: list[str] = getattr(args, "paths", [])
    report = evaluate_case_files(paths, dry_run=dry_run)
    json_output = bool(getattr(args, "json", False))

    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"harness eval: {report['total']} case(s), "
            f"valid: {report['valid']}, invalid: {report['invalid']}"
        )
        for error in report["errors"]:
            source = error.get("source", "")
            message = error.get("message", "")
            if source:
                print(f"  {source}: {message}")
            else:
                print(f"  {message}")

    return 0 if report["invalid"] == 0 else 1


def _main_entry() -> int:
    """Convenience entry point for direct invocation without the full CLI."""
    parser = argparse.ArgumentParser(prog="hermes harness eval")
    parser.add_argument("paths", nargs="+", help="JSONL case file(s) or glob patterns")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--json", action="store_true", default=False)
    args = parser.parse_args()
    args.harness_command = "eval"
    return cmd_harness(args)


if __name__ == "__main__":
    sys.exit(_main_entry())
