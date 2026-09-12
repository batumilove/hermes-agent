"""Operator-facing lifecycle lease inspection and reconciliation."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from typing import Any

from gateway.lifecycle_lease import (
    LifecycleLeaseBlocked,
    acquire_lifecycle_lease,
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


def _run_external_controller(args) -> int:
    command = list(args.external_command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("external controller command is required after --")

    try:
        expires_at = datetime.fromisoformat(args.expires_at)
    except ValueError as exc:
        raise ValueError("expires-at must be valid ISO-8601") from exc
    if expires_at.tzinfo is None:
        raise ValueError("expires-at must be timezone-aware")

    lease = acquire_lifecycle_lease(
        home=get_hermes_home(),
        owner_token=args.owner_token,
        purpose=args.purpose,
        provenance={
            "source_head": args.source_head,
            "source_tree": args.source_tree,
            "artifact_sha256": args.artifact_sha256,
            "evidence_id": args.evidence_id,
        },
        expires_at=expires_at,
    )
    primary: BaseException | None = None
    try:
        result = subprocess.run(command, check=False)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            lease.release()
        except BaseException as release_exc:
            if primary is not None:
                raise release_exc from primary
            raise
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


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
        if args.lifecycle_lease_command == "run":
            return _run_external_controller(args)
        raise ValueError("unknown lifecycle lease command")
    except (LifecycleLeaseBlocked, ValueError) as exc:
        _emit(
            {"status": "blocked", "error": str(exc)},
            as_json=as_json,
            error=True,
        )
        return 2
