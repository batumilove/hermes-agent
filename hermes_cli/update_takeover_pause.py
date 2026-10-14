"""Stdlib-only quiescence for a historical updater's captured bare Linux gateway.

Historical POSIX releases have already swapped the tree when this runs. Reject a
runtime we cannot authenticate; never let slow preparation widen that live-code
window. Supervised gateways require the current updater's pre-swap pause instead.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import struct
import sys
import time
from datetime import datetime

from gateway.control_socket import CONTROL_PROTOCOL_VERSION, _read_response_line, resolve_client_socket_path
from hermes_cli import update_lock, update_pause_record


class TakeoverPauseBlocked(RuntimeError):
    """The already-swapped update still owes a safe gateway handoff."""


def _query(home: Path, pid: int, verb: str) -> dict:
    path = resolve_client_socket_path(home)
    if path is None:
        raise TakeoverPauseBlocked(f"captured gateway {pid} has no control socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        peer, _, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer != pid:
            raise TakeoverPauseBlocked("gateway control socket names another process")
        connection.sendall(json.dumps({"verb": verb, "id": 1, "protocol": CONTROL_PROTOCOL_VERSION}).encode() + b"\n")
        raw = _read_response_line(lambda: connection.recv(65536), time.monotonic() + 5)
    response = json.loads(raw) if raw else None
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise TakeoverPauseBlocked(f"gateway did not acknowledge {verb}")
    return response["result"]


def _proc_bytes(pid: int, name: str) -> bytes:
    with Path(f"/proc/{pid}/{name}").open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise TakeoverPauseBlocked(f"gateway {name} exceeds the identity bound")
    return content


def _native_identity(pid: int) -> tuple[int, int]:
    fields = _proc_bytes(pid, "stat").decode("utf-8").rsplit(")", 1)[1].split()
    return int(fields[19]), int(fields[1])


def _replay_args(argv: list[str], root: Path, pid: int) -> list[str]:
    try:
        index = argv.index("gateway")
    except ValueError as exc:
        raise TakeoverPauseBlocked("captured command is not gateway run") from exc
    if argv[index:index + 2] != ["gateway", "run"] or "--external-supervisor" in argv:
        raise TakeoverPauseBlocked("captured gateway is externally supervised")
    # Either an install-owned executable/script or module execution from this
    # checkout proves the command belongs to the source the updater moved.
    owned = any(Path(arg).is_absolute() and Path(arg).is_relative_to(root) for arg in argv[:index])
    module = ["-m", "hermes_cli.main"]
    if not owned and not (any(argv[i:i + 2] == module for i in range(index))
                          and Path(f"/proc/{pid}/cwd").resolve() == root):
        raise TakeoverPauseBlocked("gateway argv does not identify this installation")
    for offset in range(index):
        if argv[offset:offset + 2] == module:
            return argv[offset + 2:]
        if Path(argv[offset]).name in {"hermes", "main.py"}:
            return argv[offset + 1:]
    raise TakeoverPauseBlocked("gateway command has no replayable CLI entrypoint")


def _authenticate(row: dict, request: dict, captured_at: float) -> dict:
    from hermes_constants import get_default_hermes_root, get_hermes_home

    pid = int(row["pid"])
    if pid <= 1 or row.get("supervisor") != "manual":
        raise TakeoverPauseBlocked("historical takeover cannot pause a supervised gateway")
    root = Path(request["root"]).resolve()
    profile = row.get("profile", "default")
    base = get_default_hermes_root()
    home = base if profile == "default" else base / "profiles" / profile
    if home.resolve() != get_hermes_home().resolve():
        raise TakeoverPauseBlocked("captured gateway home is outside this lifecycle lease")
    ident = _query(home, pid, "identify")
    native_start, _ = _native_identity(pid)
    birth = update_lock.process_create_time(pid)
    expected = row.get("code_sha") or request["plan"].get("expected_sha")
    if (ident.get("kind") != "hermes-gateway" or ident.get("pid") != pid
            or ident.get("supervisor") != "manual" or ident.get("start_time") != native_start
            or Path(ident.get("hermes_home", "")).resolve() != home.resolve()
            or not expected or ident.get("code_sha") != expected
            or birth is None or birth > captured_at):
        raise TakeoverPauseBlocked("captured gateway identity or incarnation changed")
    # Never stop a host whose process ancestry includes this updater, nor an
    # undeclared service/keep-alive manager. No signal/disarm fallback exists.
    ancestor = os.getpid()
    while ancestor > 1:
        if ancestor == pid:
            raise TakeoverPauseBlocked("gateway is an ancestor of this updater")
        _, ancestor = _native_identity(ancestor)
    environ = _proc_bytes(pid, "environ").split(b"\0")
    if any(item.startswith((b"INVOCATION_ID=", b"HERMES_S6_SUPERVISED_CHILD=", b"XPC_SERVICE_NAME=")) for item in environ):
        raise TakeoverPauseBlocked("gateway has an undeclared supervisor")
    argv = [os.fsdecode(arg) for arg in _proc_bytes(pid, "cmdline").split(b"\0") if arg]
    args = _replay_args(argv, root, pid)
    if _native_identity(pid)[0] != native_start:
        raise TakeoverPauseBlocked("gateway incarnation changed during authentication")
    return {"pid": pid, "home": str(home), "argv": argv, "takeover_args": args,
            "ct": native_start, "identity": f"ct:{birth:.3f}"}


def pause_captured_gateways(request: dict) -> dict | None:
    """Persist exact restart custody, then ask and prove the captured gateways exited."""
    if sys.platform == "win32" or request.get("restart_update") or request.get("no_gateway_restart"):
        return None
    rows = [row for row in (request.get("plan") or {}).get("runtimes", []) if row.get("kind") == "gateway"]
    if not rows:
        return None
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise TakeoverPauseBlocked("historical gateway native control identity is unavailable")
    captured = (request.get("receipt") or {}).get("started_at")
    try:
        when = datetime.fromisoformat(captured)
        if when.tzinfo is None:
            raise ValueError("receipt timestamp is not timezone-aware")
        entries = [_authenticate(row, request, when.timestamp()) for row in rows]
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise TakeoverPauseBlocked(f"cannot authenticate the historical gateway set: {exc}") from exc
    token = {"platform": "posix", "resume_needed": True, "posix_stopped": True,
             "profiles": {}, "posix_units": [], "unmapped": entries,
             "unmapped_pids": [entry["pid"] for entry in entries],
             "identities": {str(entry["pid"]): entry["identity"] for entry in entries}}
    update_pause_record.record_pause(token, None, [])
    update_pause_record.mark_stop_requested(token, token["unmapped_pids"])
    request["windows_resume"] = token
    for entry in entries:
        pid = entry["pid"]
        if update_lock.incarnation_live(pid, entry["identity"]) is not True:
            raise TakeoverPauseBlocked("gateway changed before the drain request")
        update_pause_record.mark_stop_sent(token, pid)
        ack = _query(Path(entry["home"]), pid, "pause-for-update")
        raw_budget = ack.get("drain_timeout")
        if isinstance(raw_budget, bool) or not isinstance(raw_budget, (int, float)):
            raise TakeoverPauseBlocked("gateway did not acknowledge a numeric pause budget")
        budget = float(raw_budget)
        if (ack.get("pid") != pid or not (ack.get("pausing") or ack.get("already_stopping"))
                or not math.isfinite(budget) or not 0 <= budget <= 3600):
            raise TakeoverPauseBlocked("gateway did not acknowledge a bounded pause")
        deadline = time.monotonic() + budget + 10
        while update_lock.incarnation_live(pid, entry["identity"]) is not False:
            if time.monotonic() >= deadline:
                raise TakeoverPauseBlocked(f"gateway {pid} has not completed its acknowledged drain")
            time.sleep(0.1)
    print("  Paused captured legacy gateways before dependency preparation", flush=True)
    return token
