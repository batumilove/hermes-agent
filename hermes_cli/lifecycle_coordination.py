"""Shared lifecycle-lease acquisition for disruptive CLI controllers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gateway.lifecycle_lease import (
    LifecycleLease,
    LifecycleLeaseBlocked,
    acquire_lifecycle_lease,
)

_MANIFEST_SCHEMA = "hermes-lifecycle-cli-transaction/v1"
_DEFAULT_LIFETIME = timedelta(hours=6)


class LifecycleCoordinationBlocked(RuntimeError):
    """A CLI lifecycle transaction cannot establish exact common ownership."""


def _git_identity(repo_root: Path) -> tuple[str, str]:
    def rev_parse(revision: str) -> str:
        process = subprocess.Popen(
            ["git", "rev-parse", revision],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            stdout, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, process.args)
        return stdout.strip()

    try:
        head = rev_parse("HEAD")
        tree = rev_parse("HEAD^{tree}")
    except (OSError, subprocess.SubprocessError) as exc:
        raise LifecycleCoordinationBlocked(
            "lifecycle transaction source identity is unavailable"
        ) from exc
    if len(head) != 40 or len(tree) != 40:
        raise LifecycleCoordinationBlocked(
            "lifecycle transaction source identity is malformed"
        )
    return head, tree


def acquire_cli_lifecycle_transaction(
    *,
    home: Path,
    repo_root: Path,
    purpose: str,
    operation: str,
    now: datetime | None = None,
) -> LifecycleLease:
    """Acquire common ownership bound to one exact CLI transaction manifest."""
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    source_head, source_tree = _git_identity(Path(repo_root))
    owner_token = f"{purpose}-{uuid.uuid4().hex}"
    manifest = {
        "evidence_id": owner_token,
        "operation": operation,
        "purpose": purpose,
        "schema": _MANIFEST_SCHEMA,
        "source_head": source_head,
        "source_tree": source_tree,
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    provenance = {
        "source_head": source_head,
        "source_tree": source_tree,
        "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
        "evidence_id": owner_token,
    }
    try:
        return acquire_lifecycle_lease(
            home=Path(home),
            owner_token=owner_token,
            purpose=purpose,
            provenance=provenance,
            expires_at=observed_now + _DEFAULT_LIFETIME,
            now=observed_now,
        )
    except LifecycleLeaseBlocked as exc:
        raise LifecycleCoordinationBlocked(str(exc)) from exc
