"""``hermes harness`` subcommand parser."""

from __future__ import annotations

import argparse
from typing import Callable


def build_harness_parser(
    subparsers, *, cmd_harness: Callable[[argparse.Namespace], int]
) -> None:
    """Attach the ``harness`` subcommand to ``subparsers``."""
    harness_parser = subparsers.add_parser(
        "harness",
        help="Run harness failure-case evaluations",
        description="Validate and evaluate harness-learning JSONL case files.",
    )
    harness_sub = harness_parser.add_subparsers(dest="harness_command")

    eval_parser = harness_sub.add_parser(
        "eval",
        help="Validate JSONL harness cases (dry-run by default)",
    )
    eval_parser.add_argument(
        "paths",
        nargs="+",
        help="One or more JSONL case files or glob patterns",
    )
    eval_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Validate without making model calls (default: True)",
    )
    eval_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit a JSON report instead of plain text",
    )
    eval_parser.set_defaults(func=cmd_harness)

    harness_parser.set_defaults(func=cmd_harness)
