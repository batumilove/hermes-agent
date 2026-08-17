"""External drain-control marker contract (dashboard → gateway).

Task 2.2 of the safe-shutdown plan (decisions.md Q-B, option A): the dashboard
has no way to call into a running gateway — there is no HTTP control channel
into the gateway process (guardrails: "there is NO external control channel
into a running gateway"). Restart/drain is driven only by the gateway reacting
to its own inputs: slash commands, process signals, and file markers it writes
itself (``.restart_notify.json``).

So the begin/cancel-drain dashboard endpoint communicates with the running
gateway the same way: it writes (or removes) a marker file, and a gateway
background watcher reacts to it. This module owns that marker contract so both
sides — the dashboard endpoint (writer) and the gateway watcher (reader) —
share one definition and can never disagree.

Contract (presence-based for the gateway, ownership-aware for controllers):

  * begin-drain  → write ``{HERMES_HOME}/.drain_request.json`` with
    ``{"action": "drain", "requested_at": <iso>, "principal": <str>,
    "epoch": <instantiation-epoch>, "suppress_notification": <bool>}``.
  * cancel-drain → remove the marker.
  * activation controllers hold a profile-scoped exclusive lock named
    ``hermes-gateway-activation-<home-hash>.lock`` in ``/run/user/<uid>`` when
    available, with a canonical ``HERMES_HOME`` fallback, for the complete
    transaction and stamp a unique ``owner_token`` into the marker. Refresh and
    cleanup validate that exact token. Dashboard/legacy writes take the same
    lock transiently, so they cannot overwrite or remove a live owned drain.
  * activation controllers additionally hold a profile-scoped drain-owner
    signal lock for the complete transaction. Routine cron admission holds only
    the activation-serialization lock, so it remains atomic against activation
    without masquerading as an external drain. If an uncoordinated writer
    removes or replaces the marker, the drain-owner lock keeps intake and cron
    paused until the owner detects the mismatch and releases deliberately.
  * The gateway watcher treats **presence of a marker stamped with the current
    instantiation epoch** as "external drain active": flip
    ``gateway_state -> "draining"`` and stop accepting new turns. Absence (or a
    marker from a *prior* instantiation) means "not draining" (revert to
    ``running`` if we had flipped it).

Why the epoch (NS-570). ``HERMES_HOME`` is a **durable** store — on Hermes
Cloud it is a persistent Fly volume (``/opt/data``). A begin-drain marker
written there *survives a machine restart*. But the disruptive lifecycle
actions a drain protects (auto-update / image migrate / env edit / profile
change) all **restart the machine**, which is exactly the signal that the drain
is over. Without the epoch, a freshly-restarted gateway re-reads the orphaned
marker on boot and parks itself right back in ``draining`` forever (NS-570: an
auto-updated instance refused every turn for ~52 min). Stamping the marker with
an identity of *this* container/VM instantiation, and ignoring a marker whose
epoch doesn't match, makes "a deliberate restart clears the drain" true by
construction — while a marker written during the *current* instantiation (the
live drain) still matches, and an s6 respawn of just the gateway (PID 1 / init
unchanged) still honours an in-flight drain.

Reading the marker never raises: a malformed/half-written file reads as
"present but contentless", which the watcher still treats as drain-active
(fail-safe toward quiescing — a corrupt begin marker must not be ignored). The
epoch check is deliberately **lenient**: it ignores a marker only on a
*definite* epoch mismatch. A marker with no epoch (legacy/corrupt/contentless),
or an environment where the epoch cannot be computed (non-Linux, no ``/proc``),
both degrade to the original presence-only behaviour — never fail-closed.

Why the max-age (#85433). The epoch handles the restart case, but it bakes in
the assumption that the action a drain protects always ends in a machine
restart. When a drain-gated action completes *without* recreating the container
and the writer never cancels the drain (writer crash, forgotten cleanup), the
orphaned marker still carries the *current* epoch — so the epoch check honours
it and the gateway bounces every inbound message forever (observed in the
field: a cloud instance refused all Telegram turns for ~3 days). The marker's
``requested_at`` timestamp is therefore also checked: a marker older than
:data:`DRAIN_REQUEST_MAX_AGE_SECONDS` reads as stale. Same leniency contract as
the epoch — only a *definite* expiry (timestamp present, parseable, and too
old) is ignored; a missing/corrupt timestamp still reads as drain-active. A
deliberately long drain has a sanctioned keep-alive: re-calling
:func:`write_drain_request` refreshes ``requested_at``.
"""
from __future__ import annotations

import functools
import errno
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX activation control only
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows only
    msvcrt = None

from hermes_constants import get_hermes_home
from utils import atomic_json_write

_log = logging.getLogger(__name__)

_DRAIN_REQUEST_FILENAME = ".drain_request.json"

# Max-age fallback for a same-epoch orphaned marker (#85433). Drain-gated
# lifecycle actions complete in minutes; an hour is comfortably past any
# legitimate drain while still bounding the wedge a leaked marker can cause
# (vs. the unbounded outage observed in the field). Long-running drains
# refresh the marker via write_drain_request() (idempotent re-write bumps
# ``requested_at``) rather than raising this bound.
DRAIN_REQUEST_MAX_AGE_SECONDS = 3600.0

# Dedup guard for the expired-marker warning: the drain watcher re-reads the
# marker every second, and an expired orphan sits on disk until removed, so
# an unconditional warning would fire ~86k times/day. Keyed by the marker's
# ``requested_at`` string — a keep-alive re-write (new timestamp) that later
# expires again logs again, which is the desired behaviour.
_expiry_logged_for: Optional[str] = None


@functools.lru_cache(maxsize=1)
def current_instantiation_epoch() -> str:
    """Identity of THIS container / VM instantiation.

    Stable for the life of the PID-1 init process — so an s6 respawn of just
    the gateway keeps the same epoch and an in-flight drain is honoured — but
    changes when the machine/container is recreated (a fresh PID 1 → a fresh
    epoch). Composed from two ``/proc`` facts:

      * the kernel **boot id** (``/proc/sys/kernel/random/boot_id``) — changes
        on a VM / microVM reboot (e.g. a Fly Firecracker machine restart);
      * **PID 1's start time** (field 22 of ``/proc/1/stat``) — changes on a
        plain ``docker restart`` (the host kernel, hence boot_id, is unchanged,
        but ``/init`` is a brand-new process).

    Together they discriminate every restart mode that matters:

      | event                          | boot_id | pid1 start | epoch  | marker |
      |--------------------------------|---------|------------|--------|--------|
      | Fly microVM reboot (auto-upd.) | changes | changes    | NEW    | reject |
      | plain ``docker restart``       | same    | changes    | NEW    | reject |
      | s6 respawn of the gateway only | same    | same       | SAME   | honour |
      | host ``hermes gateway restart``| same    | same(init) | SAME   | honour |

    The last row is intentional: a host install has no durable-volume drain
    bug, and honouring a drain across a deliberate process restart is the
    intended reversible behaviour (D4a) — PID 1 there is the long-lived init
    (systemd/launchd), so the epoch is stable.

    Returns ``""`` when neither identity source is readable (non-Linux, no
    ``/proc``). An empty epoch disables the staleness check downstream,
    degrading to the released presence-only behaviour — never fail-closed.
    Memoised: the epoch is constant for the life of the process.
    """
    boot_id = ""
    try:
        boot_id = (
            Path("/proc/sys/kernel/random/boot_id")
            .read_text(encoding="utf-8")
            .strip()
        )
    except OSError:
        pass

    pid1_start = ""
    try:
        # /proc/1/stat: "<pid> (<comm>) <state> ... <starttime@field22> ...".
        # comm can contain spaces and parens, so split on the LAST ')' and
        # index into the whitespace-delimited tail. starttime is field 22
        # (1-indexed); after the comm the tail starts at field 3, so it is the
        # tail's index 19.
        stat = Path("/proc/1/stat").read_text(encoding="utf-8")
        tail = stat.rsplit(")", 1)[1].split()
        pid1_start = tail[19]
    except (OSError, IndexError):
        pass

    if not boot_id and not pid1_start:
        return ""
    return f"{boot_id}:{pid1_start}"


def drain_request_path(home: Optional[Path] = None) -> Path:
    """Absolute path to the drain-request marker, respecting HERMES_HOME."""
    base = home if home is not None else get_hermes_home()
    return Path(base) / _DRAIN_REQUEST_FILENAME


class DrainControlBusyError(RuntimeError):
    """Another controller owns the gateway activation transaction."""


class DrainControlUnavailableError(RuntimeError):
    """No activation lock location is currently usable."""


class DrainOwnershipLostError(RuntimeError):
    """An owned marker was removed or replaced during its transaction."""


def _user_runtime_dir() -> Path:
    """Return the conventional per-user runtime directory."""
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        return Path(f"/run/user/{getuid()}")
    return get_hermes_home()  # pragma: no cover - Windows compatibility


def _canonical_home(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else get_hermes_home()
    return base.expanduser().resolve(strict=False)


def _activation_lock_name(canonical_home: Path) -> str:
    profile_id = hashlib.sha256(os.fsencode(str(canonical_home))).hexdigest()[:16]
    return f"hermes-gateway-activation-{profile_id}.lock"


def _drain_owner_lock_name(canonical_home: Path) -> str:
    profile_id = hashlib.sha256(os.fsencode(str(canonical_home))).hexdigest()[:16]
    return f"hermes-gateway-drain-owner-{profile_id}.lock"


def _fallback_activation_lock_path(home: Optional[Path] = None) -> Path:
    canonical_home = _canonical_home(home)
    return canonical_home / _activation_lock_name(canonical_home)


def _fallback_drain_owner_lock_path(home: Optional[Path] = None) -> Path:
    canonical_home = _canonical_home(home)
    return canonical_home / _drain_owner_lock_name(canonical_home)


def _preferred_profile_lock_path(
    home: Optional[Path], name_factory: Callable[[Path], str]
) -> Path:
    canonical_home = _canonical_home(home)
    runtime_dir = _user_runtime_dir()
    try:
        runtime_usable = runtime_dir.is_dir() and os.access(
            runtime_dir, os.R_OK | os.W_OK | os.X_OK
        )
    except OSError:
        runtime_usable = False
    base = runtime_dir if runtime_usable else canonical_home
    return base / name_factory(canonical_home)


def activation_lock_path(home: Optional[Path] = None) -> Path:
    """Return one canonical, profile-scoped activation lock path.

    Prefer the per-user runtime directory when it already exists and is usable.
    Otherwise place the lock in the canonical HERMES_HOME, beside the marker;
    controllers that target the same home therefore converge on the same safe
    fallback instead of interpreting an inaccessible runtime path as ownership.
    """
    return _preferred_profile_lock_path(home, _activation_lock_name)


def drain_owner_lock_path(home: Optional[Path] = None) -> Path:
    """Return the profile-scoped lock used only by real drain owners."""
    return _preferred_profile_lock_path(home, _drain_owner_lock_name)


def _lock_handle(handle: TextIOWrapper, *, shared: bool = False) -> None:
    if fcntl is not None:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
        return
    if msvcrt is not None:  # pragma: no cover - Windows only
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
                exc, "winerror", None
            ) in {32, 33, 36}:
                raise BlockingIOError(exc.errno, str(exc)) from exc
            raise
        return
    raise OSError("no supported file-lock implementation")


def _unlock_handle(handle: TextIOWrapper) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows only
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _lock_handle_with_retry(handle: TextIOWrapper, *, shared: bool = False) -> None:
    """Acquire a lock despite a bounded transient inspection collision."""
    for attempt in range(3):
        try:
            if shared:
                _lock_handle(handle, shared=True)
            else:
                _lock_handle(handle)
            return
        except BlockingIOError:
            if attempt == 2:
                raise
            time.sleep(0.01)


@dataclass
class _ActivationLock:
    """All usable aliases for one profile's activation lock."""

    handles: list[TextIOWrapper]

    @property
    def closed(self) -> bool:
        return all(handle.closed for handle in self.handles)


def _release_activation_lock(lock: _ActivationLock) -> None:
    first_error: Optional[OSError] = None
    for handle in reversed(lock.handles):
        if handle.closed:
            continue
        try:
            _unlock_handle(handle)
        except OSError as exc:
            if first_error is None:
                first_error = exc
        finally:
            handle.close()
    if first_error is not None:
        raise first_error


def _acquire_profile_lock(
    *,
    primary: Path,
    fallback: Path,
    label: str,
    shared: bool = False,
) -> _ActivationLock:
    paths = [primary] if primary == fallback else [primary, fallback]
    last_error: Optional[OSError] = None
    handles: list[TextIOWrapper] = []
    fallback_acquired = False

    for path in paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+", encoding="utf-8")
        except OSError as exc:
            last_error = exc
            continue
        try:
            _lock_handle_with_retry(handle, shared=shared)
        except BlockingIOError as exc:
            handle.close()
            _release_activation_lock(_ActivationLock(handles))
            raise DrainControlBusyError(
                f"gateway {label} lock is already held: {path}"
            ) from exc
        except OSError as exc:
            handle.close()
            last_error = exc
            continue
        handles.append(handle)
        if path == fallback:
            fallback_acquired = True

    if fallback_acquired:
        return _ActivationLock(handles)

    if handles:
        _release_activation_lock(_ActivationLock(handles))

    raise DrainControlUnavailableError(
        f"cannot open gateway {label} lock: {paths[-1]}"
    ) from last_error


def _acquire_activation_lock(
    home: Optional[Path] = None, *, shared: bool = False
) -> _ActivationLock:
    return _acquire_profile_lock(
        primary=activation_lock_path(home),
        fallback=_fallback_activation_lock_path(home),
        label="activation",
        shared=shared,
    )


def _acquire_drain_owner_lock(
    home: Optional[Path] = None, *, shared: bool = False
) -> _ActivationLock:
    return _acquire_profile_lock(
        primary=drain_owner_lock_path(home),
        fallback=_fallback_drain_owner_lock_path(home),
        label="drain-owner",
        shared=shared,
    )


@contextmanager
def _activation_probe_guard(home: Optional[Path] = None):
    """Serialize Windows probes; POSIX probes use compatible shared locks."""
    if fcntl is not None or msvcrt is None:
        yield
        return

    path = _fallback_activation_lock_path(home).with_suffix(".probe.lock")
    with path.open("a+b") as handle:  # pragma: no cover - Windows only
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def activation_lock_held(*, home: Optional[Path] = None) -> bool:
    """Return whether the activation-serialization lock is held exclusively."""
    try:
        with _activation_probe_guard(home):
            handle = _acquire_activation_lock(home, shared=fcntl is not None)
            _release_activation_lock(handle)
            return False
    except DrainControlBusyError:
        return True
    except DrainControlUnavailableError as exc:
        _log.error("drain-control: cannot inspect activation lock: %s", exc)
        return False
    except OSError as exc:
        _log.error("drain-control: cannot inspect activation lock: %s", exc)
        return True


def drain_owner_lock_held(*, home: Optional[Path] = None) -> bool:
    """Return whether a real drain owner holds its dedicated signal lock."""
    try:
        with _activation_probe_guard(home):
            handle = _acquire_drain_owner_lock(home, shared=fcntl is not None)
            _release_activation_lock(handle)
            return False
    except DrainControlBusyError:
        return True
    except DrainControlUnavailableError as exc:
        _log.error("drain-control: cannot inspect drain-owner lock: %s", exc)
        return False
    except OSError as exc:
        _log.error("drain-control: cannot inspect drain-owner lock: %s", exc)
        return True


def _drain_payload(
    *,
    principal: str,
    suppress_notification: bool,
    owner_token: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "drain",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "principal": principal,
        "epoch": current_instantiation_epoch(),
        "suppress_notification": bool(suppress_notification),
    }
    if owner_token is not None:
        payload["owner_token"] = owner_token
    return payload


@dataclass
class DrainOwnership:
    """Exclusive generation-scoped ownership of one activation drain."""

    principal: str
    owner_token: str
    home: Optional[Path]
    suppress_notification: bool
    _lock_handle: Optional[_ActivationLock]
    _drain_signal_handle: Optional[_ActivationLock]

    def _require_lock(self) -> None:
        if (
            self._lock_handle is None
            or self._lock_handle.closed
            or self._drain_signal_handle is None
            or self._drain_signal_handle.closed
        ):
            raise DrainOwnershipLostError("activation ownership lock is not held")

    def assert_request_owned(self) -> dict[str, Any]:
        self._require_lock()
        body = read_drain_request(home=self.home)
        if body is None:
            raise DrainOwnershipLostError("owned drain marker was removed")
        if body.get("owner_token") != self.owner_token:
            raise DrainOwnershipLostError("owned drain marker was replaced")
        return body

    def write_request(self) -> dict[str, Any]:
        self._require_lock()
        existing = read_drain_request(home=self.home)
        existing_owner = existing.get("owner_token") if existing is not None else None
        if (
            existing_owner is not None
            and existing_owner != self.owner_token
            and not _marker_is_stale(existing)
        ):
            raise DrainOwnershipLostError("refusing to replace a drain marker owned elsewhere")
        payload = _drain_payload(
            principal=self.principal,
            suppress_notification=self.suppress_notification,
            owner_token=self.owner_token,
        )
        atomic_json_write(drain_request_path(self.home), payload)
        return payload

    def refresh_request(self) -> dict[str, Any]:
        self.assert_request_owned()
        return self.write_request()

    def clear_request(self) -> bool:
        self.assert_request_owned()
        try:
            drain_request_path(self.home).unlink()
        except FileNotFoundError as exc:
            raise DrainOwnershipLostError("owned drain marker disappeared before cleanup") from exc
        return True

    def release(self) -> None:
        handle = self._lock_handle
        signal_handle = self._drain_signal_handle
        self._lock_handle = None
        self._drain_signal_handle = None
        first_error: Optional[OSError] = None
        if signal_handle is not None and not signal_handle.closed:
            try:
                _release_activation_lock(signal_handle)
            except OSError as exc:
                first_error = exc
        if handle is not None and not handle.closed:
            try:
                _release_activation_lock(handle)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "DrainOwnership":
        self._require_lock()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Never clear implicitly: exceptions leave the gateway drained.
        self.release()


def acquire_drain_ownership(
    *,
    principal: str,
    home: Optional[Path] = None,
    suppress_notification: bool = False,
    owner_token: Optional[str] = None,
) -> DrainOwnership:
    """Acquire the one canonical activation lock without changing the marker."""
    handle = _acquire_activation_lock(home)
    try:
        signal_handle = _acquire_drain_owner_lock(home)
    except Exception:
        _release_activation_lock(handle)
        raise
    return DrainOwnership(
        principal=principal,
        owner_token=owner_token or uuid.uuid4().hex,
        home=home,
        suppress_notification=bool(suppress_notification),
        _lock_handle=handle,
        _drain_signal_handle=signal_handle,
    )


def write_drain_request(
    *,
    principal: str = "drain-control",
    suppress_notification: bool = False,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    """Write the begin-drain marker. Returns the payload written.

    Takes the activation lock transiently and writes atomically so the gateway
    watcher never reads a half-written file. A live owned activation holds that
    lock continuously, so competing dashboard/legacy writes fail with
    :class:`DrainControlBusyError` rather than replacing its marker.

    Idempotent: re-writing while a drain is already in progress refreshes
    ``requested_at`` — the sanctioned keep-alive for a drain that legitimately
    needs longer than :data:`DRAIN_REQUEST_MAX_AGE_SECONDS`.

    Stamps the marker with :func:`current_instantiation_epoch` so a marker that
    later survives a machine restart on the durable HERMES_HOME volume can be
    recognised as stale and ignored (NS-570).

    ``suppress_notification`` is a generic "be quiet on the shutdown that ends
    this drain" flag. When the drain culminates in a process exit (e.g. NAS
    recreates the machine for an auto-update image migration), the gateway's
    shutdown path reads it via :func:`drain_notification_suppressed` and skips
    the *home-channel* "gateway shutting down" broadcast — the operator-flavoured
    ping that would otherwise fire on every routine auto-update, potentially
    dozens of times a day. It NEVER suppresses the per-active-session interrupt
    ping. The gateway stays agnostic about *why* the drain is quiet; the policy
    of which drain causes set the flag lives entirely in the caller (NAS). The
    field defaults False so legacy/operator drains behave exactly as before.
    """
    handle = _acquire_activation_lock(home)
    try:
        payload = _drain_payload(
            principal=principal,
            suppress_notification=suppress_notification,
        )
        atomic_json_write(drain_request_path(home), payload)
        return payload
    finally:
        _release_activation_lock(handle)


def clear_drain_request(*, home: Optional[Path] = None) -> bool:
    """Remove the drain marker (cancel-drain). Returns True if one existed.

    Takes the activation lock transiently. A live owned activation therefore
    cannot be cancelled by a competing dashboard/legacy controller. Once an
    owner exits and the OS releases its lock, an operator can clear an orphaned
    marker. Best-effort: a missing file is not an error (cancel is idempotent).

    Raises :class:`DrainControlBusyError` when an activation owner has the lock,
    or :class:`DrainControlUnavailableError` when no canonical lock location can
    be used.
    """
    handle = _acquire_activation_lock(home)
    path = drain_request_path(home)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        _log.warning("drain-control: failed to remove %s: %s", path, e)
        return False
    finally:
        _release_activation_lock(handle)


def _marker_epoch_is_stale(body: dict[str, Any]) -> bool:
    """True iff ``body``'s epoch is a *definite* mismatch with this process.

    Lenient by design — returns False (i.e. "not stale, honour it") whenever it
    can't be sure:
      * the current epoch can't be computed ("" fallback, no /proc), OR
      * the marker carries no epoch (legacy marker, or a corrupt/contentless
        ``{}`` body).
    Only a marker whose epoch is present AND differs from the current
    instantiation epoch is considered stale. This preserves the
    fail-safe-toward-quiescing contract for malformed markers.
    """
    current = current_instantiation_epoch()
    if not current:
        return False
    marker_epoch = body.get("epoch")
    if not marker_epoch:
        return False
    return marker_epoch != current


def _marker_is_expired(body: dict[str, Any]) -> bool:
    """True iff ``body``'s ``requested_at`` is *definitely* too old (#85433).

    The max-age fallback for a same-epoch orphan: a drain-gated action that
    completes WITHOUT a machine restart leaves a marker the epoch check cannot
    reject, and if the writer never cancels the drain the gateway is wedged in
    ``draining`` for the life of the container. Bounding the marker's lifetime
    by :data:`DRAIN_REQUEST_MAX_AGE_SECONDS` converts that unbounded outage
    into a self-healing one.

    Same leniency contract as :func:`_marker_epoch_is_stale` — returns False
    ("not expired, honour it") whenever it can't be sure:
      * the marker carries no ``requested_at`` (legacy/corrupt/contentless
        body), OR
      * the timestamp isn't a parseable ISO-8601 string.
    Only a timestamp that parses AND lies more than the max-age in the past is
    considered expired. A future-dated timestamp (clock skew) is honoured. The
    expiry is logged loudly — but once per marker, not per poll: the gateway's
    drain watcher re-reads the marker every second, and an expired orphan stays
    on disk until an operator or writer removes it, so an unconditional warning
    would repeat ~86k times/day. This path only fires when a writer leaked a
    marker, and the log is the operator's breadcrumb.
    """
    global _expiry_logged_for
    raw = body.get("requested_at")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        requested_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - requested_at).total_seconds()
    if age <= DRAIN_REQUEST_MAX_AGE_SECONDS:
        return False
    if _expiry_logged_for != raw:
        _expiry_logged_for = raw
        _log.warning(
            "drain-control: ignoring expired drain marker (requested_at=%s, "
            "age=%.0fs > max %.0fs, principal=%s) — the drain that wrote it "
            "was never cancelled; treating as stale so the gateway keeps "
            "accepting turns.",
            raw,
            age,
            DRAIN_REQUEST_MAX_AGE_SECONDS,
            body.get("principal"),
        )
    return True


def _marker_is_stale(body: dict[str, Any]) -> bool:
    """True iff the marker is definitely from a drain that is already over.

    Two independent, individually-lenient signals (either suffices):
      * epoch mismatch — the marker survived a machine restart (NS-570);
      * expiry — a same-epoch orphan outlived any legitimate drain (#85433).
    """
    return _marker_epoch_is_stale(body) or _marker_is_expired(body)


def drain_requested(*, home: Optional[Path] = None) -> bool:
    """True iff a begin-drain marker for THIS instantiation is present.

    A marker whose ``epoch`` does not match the current instantiation epoch is
    treated as absent: it survived a container/VM restart (HERMES_HOME is a
    durable Fly volume on Hermes Cloud) and the lifecycle action that triggered
    the drain has already completed — honouring it would wedge the
    freshly-restarted gateway in ``draining`` (NS-570). A marker whose
    ``requested_at`` is older than :data:`DRAIN_REQUEST_MAX_AGE_SECONDS` is
    likewise treated as absent: it is a same-epoch orphan whose drain-gated
    action completed without a restart and was never cancelled (#85433). Both
    staleness checks are lenient (see :func:`_marker_epoch_is_stale` /
    :func:`_marker_is_expired`): a legacy/corrupt marker with no epoch and no
    timestamp, or an environment without ``/proc``, still reads as
    drain-active. A dedicated drain-owner lock is also a drain signal so an
    owner remains fail-closed if its marker is removed or replaced. Transient
    writers, cancellers, and routine cron admission hold only the separate
    activation serialization lock and therefore do not make the gateway enter
    draining. Lock inspection performs filesystem lock operations on each
    call.
    """
    if drain_owner_lock_held(home=home):
        return True
    body = read_drain_request(home=home)
    if body is None:
        return False
    if _marker_is_stale(body):
        return False
    return True


@contextmanager
def cron_admission(*, home: Optional[Path] = None):
    """Atomically decide and reserve one cron admission boundary.

    The activation lock is held from the drain-state decision until the caller
    has advanced/claimed the schedule and registered executor work. An
    activation transaction therefore starts either before admission (and this
    yields ``False``) or after registration, never in between.
    """
    try:
        lock = _acquire_activation_lock(home)
    except DrainControlBusyError:
        yield False
        return
    except (DrainControlUnavailableError, OSError) as exc:
        _log.error("drain-control: cannot reserve cron admission: %s", exc)
        yield False
        return

    try:
        body = read_drain_request(home=home)
        yield body is None or _marker_is_stale(body)
    finally:
        _release_activation_lock(lock)


def drain_notification_suppressed(*, home: Optional[Path] = None) -> bool:
    """True iff an ACTIVE drain marker asks to suppress the shutdown broadcast.

    "Active" means exactly what :func:`drain_requested` means — a marker present
    AND stamped with the current instantiation epoch AND not past its max-age.
    A stale (other-epoch) marker that survived a machine restart on the durable
    HERMES_HOME volume, or an expired same-epoch orphan (#85433), is
    ignored here just as it is for drain state (NS-570): we must never let an
    orphaned marker's flag silence a *fresh* gateway's legitimate shutdown
    broadcast.

    Only honours the flag when it is explicitly truthy in the marker body. A
    legacy marker without the field, a corrupt/contentless ``{}`` body, or an
    absent marker all read as "not suppressed" (False) — fail toward the louder,
    more-visible behaviour, consistent with :func:`read_drain_request`'s
    never-raise contract. The gateway's shutdown path uses this to skip ONLY the
    home-channel broadcast; the per-active-session interrupt ping is unaffected.
    """
    body = read_drain_request(home=home)
    if body is None:
        return False
    if _marker_is_stale(body):
        return False
    return bool(body.get("suppress_notification"))


def read_drain_request(*, home: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Return the marker payload, or ``None`` if absent.

    A present-but-unparseable marker returns ``{}`` (truthy-presence preserved
    via :func:`drain_requested`; callers that need the body get an empty dict
    rather than an exception). Never raises.
    """
    path = drain_request_path(home)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        _log.warning("drain-control: failed to read %s: %s", path, e)
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
