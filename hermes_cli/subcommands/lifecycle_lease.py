"""Parser for the operator-facing ``hermes lifecycle-lease`` command."""

from __future__ import annotations

import argparse
from typing import Callable

_PURPOSES = (
    "bounded-restart",
    "checkout-reconciliation",
    "deployment",
    "gateway-restart",
    "lcm-activation",
    "soak",
)


def build_lifecycle_lease_parser(subparsers, *, cmd_lifecycle_lease: Callable) -> None:
    parser = subparsers.add_parser(
        "lifecycle-lease",
        help="Inspect or reconcile the active profile's lifecycle lease",
        description=(
            "Inspect lifecycle-controller ownership without mutation, or remove "
            "one exact valid orphan after confirming its hash, owner, and purpose."
        ),
    )
    commands = parser.add_subparsers(
        dest="lifecycle_lease_command", required=True
    )

    inspect_parser = commands.add_parser(
        "inspect", help="Read lifecycle lease and live-lock state"
    )
    inspect_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    inspect_parser.set_defaults(func=cmd_lifecycle_lease)

    reconcile_parser = commands.add_parser(
        "reconcile", help="Remove one exact valid orphaned lease record"
    )
    reconcile_parser.add_argument(
        "--expected-metadata-sha256",
        required=True,
        help="Exact SHA256 printed by lifecycle-lease inspect",
    )
    reconcile_parser.add_argument(
        "--expected-owner-token",
        required=True,
        help="Exact owner token printed by lifecycle-lease inspect",
    )
    reconcile_parser.add_argument(
        "--expected-purpose",
        required=True,
        choices=_PURPOSES,
        help="Exact purpose printed by lifecycle-lease inspect",
    )
    reconcile_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    reconcile_parser.set_defaults(func=cmd_lifecycle_lease)

    run_parser = commands.add_parser(
        "run",
        help="Run an external LCM activation or soak controller under a lease",
        description=(
            "Acquire exact profile-wide lifecycle ownership, run one external "
            "controller without a shell, and release ownership on exit."
        ),
    )
    run_parser.add_argument(
        "--purpose", required=True, choices=("lcm-activation", "soak")
    )
    run_parser.add_argument("--owner-token", required=True)
    run_parser.add_argument("--source-head", required=True)
    run_parser.add_argument("--source-tree", required=True)
    run_parser.add_argument("--artifact-sha256", required=True)
    run_parser.add_argument("--evidence-id", required=True)
    run_parser.add_argument(
        "--expires-at",
        required=True,
        help="Timezone-aware ISO-8601 authorization expiry",
    )
    run_parser.add_argument(
        "external_command",
        nargs=argparse.REMAINDER,
        help="Command and arguments after -- (executed directly, never via a shell)",
    )
    run_parser.set_defaults(func=cmd_lifecycle_lease)
