"""``hermes skill-usage`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_skill_usage_parser(subparsers, *, cmd_skill_usage: Callable) -> None:
    """Attach the ``skill-usage`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "skill-usage",
        help="Show skill usage and context-cost report",
        description=(
            "Analyze session history to report skill_view loads, fixed "
            "<available_skills> index cost, and loaded skill payload context."
        ),
    )
    parser.add_argument(
        "--days", type=int, default=30, help="Number of days to analyze (default: 30)"
    )
    parser.add_argument(
        "--source", help="Filter by platform (cli, telegram, discord, etc.)"
    )
    parser.add_argument(
        "--profile", help="Analyze state.db for the named Hermes profile"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    parser.add_argument(
        "--csv", action="store_true", help="Output machine-readable CSV"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Limit for top-skills/co-occurrence display (default: 10)",
    )
    parser.set_defaults(func=cmd_skill_usage)
