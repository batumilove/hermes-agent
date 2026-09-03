from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from gateway.lifecycle_lease import (
    LifecycleLeaseBlocked,
    acquire_lifecycle_lease,
    inspect_lifecycle_lease,
    reconcile_lifecycle_lease,
)


_PROVENANCE = {
    "source_head": "a" * 40,
    "source_tree": "b" * 40,
    "artifact_sha256": "c" * 64,
    "evidence_id": "lease-cli-test",
}


def _acquire(home, *, owner_token="owner-1"):
    return acquire_lifecycle_lease(
        home=home,
        owner_token=owner_token,
        purpose="deployment",
        provenance=_PROVENANCE,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def test_inspect_absent_is_read_only(tmp_path):
    home = tmp_path / "missing-home"

    observed = inspect_lifecycle_lease(home=home)

    assert observed == {
        "status": "absent",
        "lock_state": "absent",
        "metadata": None,
        "metadata_sha256": None,
    }
    assert not home.exists()


def test_inspect_reports_active_owner_without_mutation(tmp_path):
    lease = _acquire(tmp_path)
    metadata_path = tmp_path / ".lifecycle_transaction_lease.json"
    before = metadata_path.read_bytes()
    try:
        observed = inspect_lifecycle_lease(home=tmp_path)
    finally:
        lease.release()

    assert observed["status"] == "active"
    assert observed["lock_state"] == "busy"
    assert observed["metadata"]["owner_token"] == "owner-1"
    assert observed["metadata"]["purpose"] == "deployment"
    assert len(observed["metadata_sha256"]) == 64
    assert before == json.dumps(
        observed["metadata"], sort_keys=True, indent=2
    ).encode() + b"\n"


def test_inspect_reports_valid_metadata_without_live_lock_as_orphaned(tmp_path):
    lease = _acquire(tmp_path)
    lease._handle.close()

    observed = inspect_lifecycle_lease(home=tmp_path)

    assert observed["status"] == "orphaned"
    assert observed["lock_state"] == "available"
    assert observed["metadata"]["owner_token"] == "owner-1"


def test_inspect_blocks_symlinked_metadata_without_reading_target(tmp_path):
    target = tmp_path / "secret.json"
    target.write_text('{"secret":"must-not-parse"}', encoding="utf-8")
    (tmp_path / ".lifecycle_transaction_lease.json").symlink_to(target)

    observed = inspect_lifecycle_lease(home=tmp_path)

    assert observed["status"] == "blocked"
    assert observed["metadata"] is None
    assert observed["metadata_sha256"] is None
    assert "unsafe" in observed["reason"]


def test_reconcile_refuses_live_owner_even_with_exact_hash(tmp_path):
    lease = _acquire(tmp_path)
    observed = inspect_lifecycle_lease(home=tmp_path)
    try:
        with pytest.raises(LifecycleLeaseBlocked, match="active"):
            reconcile_lifecycle_lease(
                home=tmp_path,
                expected_metadata_sha256=observed["metadata_sha256"],
                expected_owner_token="owner-1",
                expected_purpose="deployment",
            )
        assert (tmp_path / ".lifecycle_transaction_lease.json").exists()
    finally:
        lease.release()


def test_reconcile_refuses_changed_or_mistyped_metadata_identity(tmp_path):
    lease = _acquire(tmp_path)
    lease._handle.close()

    with pytest.raises(LifecycleLeaseBlocked, match="identity"):
        reconcile_lifecycle_lease(
            home=tmp_path,
            expected_metadata_sha256="0" * 64,
            expected_owner_token="owner-1",
            expected_purpose="deployment",
        )

    assert (tmp_path / ".lifecycle_transaction_lease.json").exists()


def test_reconcile_removes_only_exact_valid_orphan(tmp_path):
    lease = _acquire(tmp_path)
    lease._handle.close()
    observed = inspect_lifecycle_lease(home=tmp_path)

    result = reconcile_lifecycle_lease(
        home=tmp_path,
        expected_metadata_sha256=observed["metadata_sha256"],
        expected_owner_token="owner-1",
        expected_purpose="deployment",
    )

    assert result == {
        "status": "reconciled",
        "owner_token": "owner-1",
        "purpose": "deployment",
        "metadata_sha256": observed["metadata_sha256"],
    }
    assert not (tmp_path / ".lifecycle_transaction_lease.json").exists()
    assert inspect_lifecycle_lease(home=tmp_path)["status"] == "absent"


def test_reconcile_refuses_malformed_orphan(tmp_path):
    metadata_path = tmp_path / ".lifecycle_transaction_lease.json"
    metadata_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(LifecycleLeaseBlocked, match="malformed"):
        reconcile_lifecycle_lease(
            home=tmp_path,
            expected_metadata_sha256="0" * 64,
            expected_owner_token="owner-1",
            expected_purpose="deployment",
        )

    assert metadata_path.exists()


def test_reconcile_refuses_mismatched_owner_or_purpose_confirmation(tmp_path):
    lease = _acquire(tmp_path)
    lease._handle.close()
    observed = inspect_lifecycle_lease(home=tmp_path)

    with pytest.raises(LifecycleLeaseBlocked, match="confirmation"):
        reconcile_lifecycle_lease(
            home=tmp_path,
            expected_metadata_sha256=observed["metadata_sha256"],
            expected_owner_token="another-owner",
            expected_purpose="deployment",
        )

    assert (tmp_path / ".lifecycle_transaction_lease.json").exists()
