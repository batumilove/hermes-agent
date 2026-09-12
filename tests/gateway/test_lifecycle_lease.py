from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import gateway.lifecycle_lease as lifecycle_lease

from gateway.lifecycle_lease import (
    LifecycleLeaseBlocked,
    acquire_lifecycle_lease,
)


NOW = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)


def acquire(home, *, token="txn-a", purpose="bounded-restart"):
    return acquire_lifecycle_lease(
        home=home,
        owner_token=token,
        purpose=purpose,
        provenance={
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "artifact_sha256": "c" * 64,
            "evidence_id": token,
        },
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )


@pytest.mark.parametrize(
    "purpose",
    [
        "bounded-restart",
        "checkout-reconciliation",
        "deployment",
        "gateway-restart",
        "lcm-activation",
        "soak",
    ],
)
def test_soak_lease_excludes_every_competing_lifecycle_controller(tmp_path, purpose):
    home = tmp_path / "home"
    first = acquire(home, purpose="soak")
    try:
        with pytest.raises(LifecycleLeaseBlocked, match="txn-a"):
            acquire(home, token="txn-b", purpose=purpose)

        metadata = json.loads((home / ".lifecycle_transaction_lease.json").read_text())
        assert metadata["owner_token"] == "txn-a"
        assert metadata["purpose"] == "soak"
        assert metadata["state"] == "ACQUIRED"
        assert metadata["provenance"]["artifact_sha256"] == "c" * 64
    finally:
        first.release()


def test_lifecycle_lease_release_allows_next_controller(tmp_path):
    home = tmp_path / "home"
    first = acquire(home)
    first.release()

    second = acquire(home, token="txn-b", purpose="soak")
    try:
        assert second.owner_token == "txn-b"
    finally:
        second.release()


def test_stale_orphaned_metadata_fails_closed_until_explicit_recovery(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".lifecycle_transaction_lease.json").write_text(
        json.dumps({
            "schema": "hermes-lifecycle-transaction-lease/v1",
            "owner_token": "crashed-soak",
            "purpose": "soak",
            "state": "ACQUIRED",
            "pid": 999999,
            "acquired_at": (NOW - timedelta(hours=2)).isoformat(),
            "expires_at": (NOW - timedelta(hours=1)).isoformat(),
            "provenance": {
                "source_head": "a" * 40,
                "source_tree": "b" * 40,
                "artifact_sha256": "c" * 64,
                "evidence_id": "crashed-soak",
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(LifecycleLeaseBlocked, match="orphaned or stale"):
        acquire(home, token="txn-b")


def test_malformed_or_foreign_metadata_fails_closed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".lifecycle_transaction_lease.json").write_text("{}", encoding="utf-8")

    with pytest.raises(LifecycleLeaseBlocked, match="malformed"):
        acquire(home)


@pytest.mark.parametrize("tamper", ["owner", "provenance", "pid", "expires_at"])
def test_release_refuses_foreign_metadata_and_preserves_it(tmp_path, tamper):
    home = tmp_path / "home"
    lease = acquire(home)
    metadata_path = home / ".lifecycle_transaction_lease.json"
    metadata = json.loads(metadata_path.read_text())
    if tamper == "owner":
        metadata["owner_token"] = "foreign"
    elif tamper == "provenance":
        metadata["provenance"]["artifact_sha256"] = "d" * 64
    elif tamper == "pid":
        metadata["pid"] += 1
    else:
        metadata["expires_at"] = (NOW + timedelta(hours=2)).isoformat()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(LifecycleLeaseBlocked, match="ownership changed"):
        lease.release()
    assert metadata_path.exists()


def test_invalid_owner_and_purpose_are_rejected(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(ValueError, match="owner_token"):
        acquire_lifecycle_lease(
            home=home,
            owner_token="../escape",
            purpose="soak",
            provenance={
                "source_head": "a" * 40,
                "source_tree": "b" * 40,
                "artifact_sha256": "c" * 64,
                "evidence_id": "txn-a",
            },
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
    with pytest.raises(ValueError, match="purpose"):
        acquire_lifecycle_lease(
            home=home,
            owner_token="txn-a",
            purpose="unknown",
            provenance={
                "source_head": "a" * 40,
                "source_tree": "b" * 40,
                "artifact_sha256": "c" * 64,
                "evidence_id": "txn-a",
            },
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )


@pytest.mark.parametrize(
    "provenance",
    [
        {},
        {
            "source_head": "A" * 40,
            "source_tree": "b" * 40,
            "artifact_sha256": "c" * 64,
            "evidence_id": "txn-a",
        },
        {
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "artifact_sha256": "c" * 63,
            "evidence_id": "txn-a",
        },
        {
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "artifact_sha256": "c" * 64,
            "evidence_id": "../escape",
        },
    ],
)
def test_rejects_ambiguous_or_malformed_provenance(tmp_path, provenance):
    with pytest.raises(ValueError, match="provenance"):
        acquire_lifecycle_lease(
            home=tmp_path / "home",
            owner_token="txn-a",
            purpose="soak",
            provenance=provenance,
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )


def test_refuses_symlink_lock_without_touching_target(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "target"
    target.write_text("do-not-touch", encoding="utf-8")
    target.chmod(0o640)
    (home / ".lifecycle_transaction_lease.lock").symlink_to(target)

    with pytest.raises(LifecycleLeaseBlocked, match="lock path"):
        acquire(home)

    assert target.read_text(encoding="utf-8") == "do-not-touch"
    assert target.stat().st_mode & 0o777 == 0o640


def test_refuses_symlink_metadata_without_touching_target(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "target"
    target.write_text("do-not-touch", encoding="utf-8")
    (home / ".lifecycle_transaction_lease.json").symlink_to(target)

    with pytest.raises(LifecycleLeaseBlocked, match="metadata is a symlink"):
        acquire(home)

    assert target.read_text(encoding="utf-8") == "do-not-touch"


def test_release_refuses_symlinked_metadata_without_reading_target(tmp_path):
    home = tmp_path / "home"
    lease = acquire(home)
    metadata_path = home / ".lifecycle_transaction_lease.json"
    metadata_path.unlink()
    target = tmp_path / "target"
    target.write_text("do-not-read-or-touch", encoding="utf-8")
    metadata_path.symlink_to(target)

    with pytest.raises(LifecycleLeaseBlocked, match="metadata is malformed"):
        lease.release()

    assert metadata_path.is_symlink()
    assert target.read_text(encoding="utf-8") == "do-not-read-or-touch"


def test_publish_oserror_is_reported_as_blocked(tmp_path, monkeypatch):
    home = tmp_path / "home"

    def fail_link(_source, _destination):
        raise OSError("filesystem does not support hard links")

    monkeypatch.setattr(lifecycle_lease.os, "link", fail_link)
    with pytest.raises(LifecycleLeaseBlocked, match="could not be published"):
        acquire(home)

    assert not (home / ".lifecycle_transaction_lease.json").exists()
