"""Operator-facing lifecycle lease inspection and reconciliation."""

from __future__ import annotations

import json
import sys
from typing import Any

from gateway.lifecycle_lease import (
    LifecycleLeaseBlocked,
    inspect_lifecycle_lease,
    reconcile_lifecycle_lease,
)
from hermes_constants import get_hermes_home


def _emit(payload: dict[str, Any], *, as_json: bool, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if as_json:
        print(json.dumps(payload, sort_keys=True), file=stream)
        return
    for key, value in payload.items():
        if key == "metadata" and isinstance(value, dict):
            print("metadata:", file=stream)
            for metadata_key, metadata_value in value.items():
                print(f"  {metadata_key}: {metadata_value}", file=stream)
        elif value is not None:
            print(f"{key}: {value}", file=stream)


def run_lifecycle_lease_command(args) -> int:
    """Run ``hermes lifecycle-lease`` against the active profile home."""
    as_json = bool(getattr(args, "json", False))
    try:
        if args.lifecycle_lease_command == "inspect":
            result = inspect_lifecycle_lease(home=get_hermes_home())
            _emit(result, as_json=as_json)
            return 2 if result["status"] == "blocked" else 0
        if args.lifecycle_lease_command == "reconcile":
            result = reconcile_lifecycle_lease(
                home=get_hermes_home(),
                expected_metadata_sha256=args.expected_metadata_sha256,
                expected_owner_token=args.expected_owner_token,
                expected_purpose=args.expected_purpose,
            )
            _emit(result, as_json=as_json)
            return 0
        raise ValueError("unknown lifecycle lease command")
    except (LifecycleLeaseBlocked, ValueError) as exc:
        _emit(
            {"status": "blocked", "error": str(exc)},
            as_json=as_json,
            error=True,
        )
        return 2
