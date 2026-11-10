"""Operator-scoped admission epochs for preserving shared-skill reconciliation.

This is a coordination protocol for participating skill writers, not an OS
sandbox. It never changes user curator locks. Old admissions remain stale after
a lease ends; expiry never silently releases a lease.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
import hashlib
import inspect
import json
import os
import secrets
import stat
import tempfile

from hermes_constants import get_hermes_home

_UNSET = object()
_admission = ContextVar("skill_write_admission", default=None)
_replay = ContextVar("skill_write_replay", default=_UNSET)
_LIMIT = 1024 * 1024


class FenceError(ValueError):
    """A write requires a fresh admission or explicit operator recovery."""


def _uid():
    getter = getattr(os, "getuid", None)
    if getter is None:
        raise FenceError("Private operator fences require POSIX ownership")
    return getter()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _home():
    return Path(get_hermes_home()).resolve()


def canonical_name(value):
    """Use the physical package directory for supported frontmatter aliases."""
    from tools import skill_manager_tool as smt
    found = smt._find_skill(value)
    return Path(found["path"]).name if found else Path(value).name


def _names(values):
    return sorted({canonical_name(v) for v in values
                   if isinstance(v, str) and Path(v).name not in {"", ".", ".."}})


def _paths():
    directory = _home() / "state"
    if directory.is_symlink():
        raise FenceError("Skill fence state directory must not be a symlink")
    return directory, directory / "skill-write-fence.json"


def _private_read(path, expected_sha256=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        s = os.fstat(stream.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != _uid() or s.st_nlink != 1 or s.st_mode & 0o077:
            raise FenceError("Unsafe private skill fence record")
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise FenceError("Oversized skill fence record")
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise FenceError("Outcome receipt authentication failed")
    return json.loads(raw)


def _read():
    _, path = _paths()
    if not path.exists() and not path.is_symlink():
        return {"version": 1, "epochs": {}, "leases": {}}
    if os.name != "posix":
        raise FenceError("An active POSIX skill fence needs operator disposition on this platform")
    try:
        state = _private_read(path)
    except FileNotFoundError:
        return {"version": 1, "epochs": {}, "leases": {}}
    except (OSError, ValueError) as exc:
        raise FenceError(f"Cannot authenticate skill fence state: {exc}") from exc
    if (not isinstance(state, dict) or state.get("version") != 1
            or not isinstance(state.get("epochs"), dict) or not isinstance(state.get("leases"), dict)):
        raise FenceError("Invalid skill fence state")
    if any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in state["epochs"].items()):
        raise FenceError("Invalid skill fence epoch")
    for lease in state["leases"].values():
        if not isinstance(lease, dict) or not isinstance(lease.get("selected"), list):
            raise FenceError("Invalid skill fence lease")
    return state


def status():
    state = _read()
    return {"profile": str(_home()), "state_sha256": _digest(state), "state": state,
            "expiry_policy": "EXPIRED_LEASE_REMAINS_FENCED_UNTIL_OWNER_RECOVERY",
            "protocol": "skill-write-admission/v1",
            "enforcement_scope": "SKILL_MANAGE_FLAT_BATCH_AND_STAGED_REPLAY_ONLY"}


def _check(binding, names, state):
    epochs = binding.get("epochs")
    if (not isinstance(epochs, dict) or any(type(v) is not int or v < 0 for v in epochs.values())
            or binding.get("profile") != str(_home()) or not set(names) <= set(epochs)):
        raise FenceError("Skill admission belongs to a different profile or scope")
    if any(set(names) & set(lease["selected"]) for lease in state["leases"].values()):
        raise FenceError("Skill package is fenced; do not retry or replay a retained payload")
    if any(binding["epochs"][name] != state["epochs"].get(name, 0) for name in names):
        raise FenceError("Stale skill admission; read and review the current package before a new write")


def _capture(names):
    state = _read()
    expected = _replay.get()
    if expected is _UNSET:
        binding = {"profile": str(_home()), "epochs": {n: state["epochs"].get(n, 0) for n in names}}
    elif expected is None:
        # Existing records remain compatible only before this scope has ever
        # been fenced. Applying an old record must not assign it a new epoch.
        binding = {"profile": str(_home()), "epochs": {n: 0 for n in names}}
    elif isinstance(expected, dict):
        binding = expected
    else:
        raise FenceError("Malformed staged skill admission")
    _check(binding, names, state)
    return binding


def _entry_names(arguments):
    default = arguments.get("name") or arguments.get("default_name")
    operations = arguments.get("operations")
    if isinstance(operations, list):
        return _names(op.get("name") or default for op in operations if isinstance(op, dict))
    return _names([default])


def guard_entry(function):
    """Capture before staging/guards; recursive batch calls retain the epoch."""
    signature = inspect.signature(function)

    @wraps(function)
    def guarded(*args, **kwargs):
        names = _entry_names(signature.bind(*args, **kwargs).arguments)
        token = None
        try:
            binding = _admission.get()
            if binding is None:
                binding = _capture(names)
                token = _admission.set(binding)
            else:
                _check(binding, names, _read())
            return function(*args, **kwargs)
        except FenceError as exc:
            return json.dumps({"success": False, "error": str(exc), "skill_write_fenced": True})
        finally:
            if token is not None:
                _admission.reset(token)
    return guarded


def recheck(names):
    """Must run inside every target mutex, before capture/write/rollback."""
    binding = _admission.get()
    if binding is None:
        raise FenceError("Missing skill write admission")
    _check(binding, _names(names), _read())


def staged_payload(payload):
    binding = _admission.get()
    if binding is not None:
        return {**payload, "_skill_write_admission": binding}
    return payload


@contextmanager
def replay_binding(payload):
    token = _replay.set(payload.get("_skill_write_admission"))
    try:
        yield
    finally:
        _replay.reset(token)


@contextmanager
def _lock(path, private=False, create=False):
    import fcntl
    if path.parent.is_symlink():
        raise FenceError("Unsafe skill fence lock parent")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_CREAT if create else 0), 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != _uid() or s.st_nlink != 1 or (private and s.st_mode & 0o077):
            raise FenceError("Unsafe skill fence lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FenceError("Skill writer or installer is active; no fence state changed") from exc
        named = path.lstat()
        if not stat.S_ISREG(named.st_mode) or (named.st_dev, named.st_ino) != (s.st_dev, s.st_ino):
            raise FenceError("Skill fence lock identity changed")
        yield
    finally:
        os.close(fd)


@contextmanager
def _operator_envelope(names):
    if os.name != "posix":
        raise FenceError("Operator admission fences require POSIX flock")
    home = _home()
    s = home.stat()
    if s.st_uid != _uid() or s.st_mode & 0o077:
        raise FenceError("Operator fence requires an owned private profile")
    from tools import skill_manager_tool as smt
    directory, _ = _paths()
    directory.mkdir(mode=0o700, exist_ok=True)
    _sync_directory(home)
    with ExitStack() as stack:
        stack.enter_context(_lock(home / "hermes-runtime-tree.lock"))
        stack.enter_context(_lock(home / ".skills.shared-skills-state" / "lock"))
        paths = sorted({smt._skill_lock_path(n, canonical=True) for n in names})
        for path in paths:
            if path.parent.is_symlink():
                raise FenceError("Unsafe package lock directory")
            path.parent.mkdir(mode=0o700, exist_ok=True)
            stack.enter_context(_lock(path, create=True))
        stack.enter_context(_lock(directory / "skill-write-fence.lock", private=True, create=True))
        yield


def _sync_directory(directory):
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _publish(state):
    directory, path = _paths()
    fd, temporary = tempfile.mkstemp(prefix=".skill-write-fence-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _capability_destination(receipt, selected):
    """Keep recovery material out of control state and installable packages."""
    from tools import skill_manager_tool as smt
    supplied = Path(receipt)
    if supplied.is_symlink():
        raise FenceError("Fence capability destination must not be a symlink")
    receipt = supplied.resolve()
    roots = [_home()]
    roots.extend(Path(found["path"]).resolve() for name in selected if (found := smt._find_skill(name)))
    if any(receipt.is_relative_to(root) for root in roots):
        raise FenceError("Fence capability must be outside the profile and selected packages")
    parent = receipt.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != _uid() or parent.st_mode & 0o077:
        raise FenceError("Fence receipt needs an existing private owner directory")
    if receipt.exists() or receipt.is_symlink():
        raise FenceError("Preserve the existing capability receipt; do not overwrite it")
    return receipt


def begin(names, *, expected_state_sha256, plan_sha256, owner, expires_at, receipt):
    selected = _names(names)
    if not selected or not owner or len(plan_sha256) != 64:
        raise FenceError("Exact scope, owner and plan digest are required")
    expiry = datetime.fromisoformat(expires_at)
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise FenceError("A future bounded expiry is required")
    receipt = _capability_destination(receipt, selected)
    with _operator_envelope(selected):
        state = _read()
        if _digest(state) != expected_state_sha256:
            raise FenceError("Fence state changed; preserve it and obtain a fresh disposition")
        if any(set(selected) & set(row["selected"]) for row in state["leases"].values()):
            raise FenceError("Existing fence owner must release its own scope")
        token = secrets.token_hex(32)
        lease_id = secrets.token_hex(16)
        row = {"profile": str(_home()), "selected": selected, "plan_sha256": plan_sha256,
               "owner": owner, "expires_at": expires_at, "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
        # Persist the recovery capability before publishing the fence. A crash
        # can leave an unused receipt, never an active lease with a lost token.
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"lease_id": lease_id, "token": token, **row}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(receipt.parent)
        for name in selected:
            state["epochs"][name] = state["epochs"].get(name, 0) + 1
        state["leases"][lease_id] = row
        _publish(state)
        return {"lease_id": lease_id, "state_sha256": _digest(state), "selected": selected,
                "drain_basis": "MUTEX_CRITICAL_SECTIONS_DRAINED_OLD_EPOCHS_INVALIDATED",
                "receipt": str(receipt), "expires_at": expires_at}


def end(receipt, *, expected_state_sha256, outcome, outcome_sha256):
    capability = _private_read(Path(receipt))
    if capability.get("profile") != str(_home()):
        raise FenceError("Fence capability belongs to another profile")
    result = _private_read(Path(outcome), outcome_sha256)
    selected = capability["selected"]
    if (result.get("lease_id") != capability["lease_id"] or result.get("plan_sha256") != capability["plan_sha256"]
            or result.get("selected") != selected or result.get("status") not in {"COMPLETE", "ABORTED_NO_MUTATION", "RECOVERED"}):
        raise FenceError("Exact scoped completion or recovery disposition required")
    with _operator_envelope(selected):
        state = _read()
        if _digest(state) != expected_state_sha256:
            raise FenceError("Fence state changed; do not restore over another owner")
        lease = state["leases"].get(capability["lease_id"])
        if lease is None or lease != {k: capability[k] for k in lease}:
            raise FenceError("Fence ownership changed")
        if hashlib.sha256(capability["token"].encode()).hexdigest() != lease["token_sha256"]:
            raise FenceError("Wrong fence recovery capability")
        for name in selected:
            state["epochs"][name] += 1
        del state["leases"][capability["lease_id"]]
        _publish(state)
        return {"lease_id": capability["lease_id"], "selected": selected, "state_sha256": _digest(state),
                "status": "OWN_LEASE_RELEASED_USER_CURATOR_LOCKS_UNCHANGED"}


def main():
    """Public operator CLI; no model tool or gateway startup is introduced."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    start = commands.add_parser("begin")
    start.add_argument("--name", action="append", required=True)
    start.add_argument("--expected-state-sha256", required=True)
    start.add_argument("--plan-sha256", required=True)
    start.add_argument("--owner", required=True)
    start.add_argument("--expires-at", required=True)
    start.add_argument("--receipt", required=True)
    finish = commands.add_parser("end")
    finish.add_argument("--receipt", required=True)
    finish.add_argument("--expected-state-sha256", required=True)
    finish.add_argument("--outcome", required=True)
    finish.add_argument("--outcome-sha256", required=True)
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        if command == "status":
            result = status()
        elif command == "begin":
            names = args.pop("name")
            result = begin(names, **args)
        else:
            result = end(**args)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Skill fence refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
