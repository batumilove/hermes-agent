"""Durable, fail-closed coordination for disruptive lifecycle transactions.

The held file lock provides live mutual exclusion.  The adjacent durable JSON
record makes crashed, expired, malformed, or foreign ownership visible rather
than silently reusing an ambiguous lease.  Recovery of an orphaned record is
an explicit operator action; this module never guesses that it is safe.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO

fcntl: Any
try:
    import fcntl as _fcntl

    fcntl = _fcntl
except ImportError:  # pragma: no cover - POSIX lifecycle control only
    fcntl = None


_SCHEMA = "hermes-lifecycle-transaction-lease/v1"
_METADATA_NAME = ".lifecycle_transaction_lease.json"
_LOCK_NAME = ".lifecycle_transaction_lease.lock"
_ALLOWED_PURPOSES = {
    "bounded-restart",
    "checkout-reconciliation",
    "deployment",
    "gateway-restart",
    "lcm-activation",
    "soak",
}
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_LOWER_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_PROVENANCE_KEYS = {
    "source_head",
    "source_tree",
    "artifact_sha256",
    "evidence_id",
}


class LifecycleLeaseBlocked(RuntimeError):
    """Lifecycle ownership is live, stale, malformed, or otherwise ambiguous."""


def _parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LifecycleLeaseBlocked(
            f"lifecycle lease metadata is malformed: {field_name}"
        ) from exc
    if parsed.tzinfo is None:
        raise LifecycleLeaseBlocked(
            f"lifecycle lease metadata is malformed: {field_name}"
        )
    return parsed.astimezone(timezone.utc)


def _read_metadata(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise LifecycleLeaseBlocked(
                "lifecycle lease metadata is not a regular file"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except LifecycleLeaseBlocked:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleLeaseBlocked("lifecycle lease metadata is malformed") from exc
    required = {
        "schema",
        "owner_token",
        "purpose",
        "state",
        "pid",
        "acquired_at",
        "expires_at",
        "provenance",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != required
        or raw.get("schema") != _SCHEMA
        or not isinstance(raw.get("owner_token"), str)
        or not _SAFE_TOKEN.fullmatch(raw["owner_token"])
        or raw.get("purpose") not in _ALLOWED_PURPOSES
        or raw.get("state") != "ACQUIRED"
        or not isinstance(raw.get("pid"), int)
        or raw["pid"] <= 0
        or not _valid_provenance(raw.get("provenance"))
    ):
        raise LifecycleLeaseBlocked("lifecycle lease metadata is malformed")
    _parse_utc(raw["acquired_at"], "acquired_at")
    _parse_utc(raw["expires_at"], "expires_at")
    return raw


def _valid_provenance(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _PROVENANCE_KEYS:
        return False
    return bool(
        isinstance(value.get("source_head"), str)
        and _LOWER_HEX_40.fullmatch(value["source_head"])
        and isinstance(value.get("source_tree"), str)
        and _LOWER_HEX_40.fullmatch(value["source_tree"])
        and isinstance(value.get("artifact_sha256"), str)
        and _LOWER_HEX_64.fullmatch(value["artifact_sha256"])
        and isinstance(value.get("evidence_id"), str)
        and _SAFE_TOKEN.fullmatch(value["evidence_id"])
    )


def _owner_summary(path: Path) -> str:
    try:
        owner = _read_metadata(path)
    except LifecycleLeaseBlocked:
        return "metadata malformed or unavailable"
    return f"owner={owner['owner_token']} purpose={owner['purpose']} pid={owner['pid']}"


def _unlock_and_close(handle: TextIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish a fully-fsynced new file without following symlinks."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".lifecycle_")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise LifecycleLeaseBlocked(
                "lifecycle lease ownership metadata appeared during acquisition"
            ) from exc
        except OSError as exc:
            raise LifecycleLeaseBlocked(
                "lifecycle lease ownership metadata could not be published"
            ) from exc
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


@dataclass
class LifecycleLease:
    home: Path
    owner_token: str
    purpose: str
    provenance: dict[str, str]
    _handle: TextIO = field(repr=False)
    _metadata: dict[str, Any] = field(repr=False)
    _released: bool = field(default=False, repr=False)

    @property
    def metadata_path(self) -> Path:
        return self.home / _METADATA_NAME

    def release(self) -> None:
        if self._released:
            return
        error: BaseException | None = None
        try:
            current = _read_metadata(self.metadata_path)
            if current != self._metadata:
                raise LifecycleLeaseBlocked(
                    "lifecycle lease ownership changed before release"
                )
            self.metadata_path.unlink()
            _fsync_directory(self.home)
        except BaseException as exc:
            error = exc
        finally:
            self._released = True
            _unlock_and_close(self._handle)
        if error is not None:
            raise error

    def __enter__(self) -> "LifecycleLease":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def acquire_lifecycle_lease(
    *,
    home: Path,
    owner_token: str,
    purpose: str,
    provenance: Mapping[str, str],
    expires_at: datetime,
    now: datetime | None = None,
) -> LifecycleLease:
    """Acquire the profile-wide lifecycle lease or fail closed.

    Existing metadata with no live lock is intentionally treated as an
    orphaned/stale transaction.  An operator must reconcile and remove that
    record explicitly; automatic expiry would permit a second controller to
    mutate state without proving what the first controller completed.
    """
    if not isinstance(owner_token, str) or not _SAFE_TOKEN.fullmatch(owner_token):
        raise ValueError("owner_token must be a safe stable identifier")
    if purpose not in _ALLOWED_PURPOSES:
        raise ValueError("purpose is not an allowed lifecycle transaction type")
    normalized_provenance = dict(provenance)
    if not _valid_provenance(normalized_provenance):
        raise ValueError(
            "provenance must contain exact source_head, source_tree, "
            "artifact_sha256, and evidence_id identities"
        )
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= current_time:
        raise ValueError("expires_at must be a future timezone-aware timestamp")
    if fcntl is None:  # pragma: no cover - Linux controller requirement
        raise LifecycleLeaseBlocked("lifecycle lease requires POSIX file locking")

    canonical_home = Path(home).expanduser().resolve(strict=False)
    canonical_home.mkdir(parents=True, exist_ok=True)
    lock_path = canonical_home / _LOCK_NAME
    metadata_path = canonical_home / _METADATA_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise LifecycleLeaseBlocked("lifecycle lease lock path is unsafe") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise LifecycleLeaseBlocked("lifecycle lease lock path is not a regular file")
    handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    try:
        os.fchmod(handle.fileno(), 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise LifecycleLeaseBlocked(
                    f"lifecycle lease is busy: {_owner_summary(metadata_path)}"
                ) from exc
            raise

        if os.path.lexists(metadata_path):
            if metadata_path.is_symlink():
                raise LifecycleLeaseBlocked(
                    "lifecycle lease ownership metadata is a symlink"
                )
            _read_metadata(metadata_path)
            raise LifecycleLeaseBlocked(
                "lifecycle lease has orphaned or stale ownership metadata"
            )

        payload = {
            "schema": _SCHEMA,
            "owner_token": owner_token,
            "purpose": purpose,
            "state": "ACQUIRED",
            "pid": os.getpid(),
            "acquired_at": current_time.isoformat(),
            "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
            "provenance": normalized_provenance,
        }
        _publish_json_exclusive(metadata_path, payload)
        return LifecycleLease(
            home=canonical_home,
            owner_token=owner_token,
            purpose=purpose,
            provenance=normalized_provenance,
            _handle=handle,
            _metadata=payload,
        )
    except BaseException:
        _unlock_and_close(handle)
        raise
