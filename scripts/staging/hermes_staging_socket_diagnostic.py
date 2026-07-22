#!/usr/bin/env python3
"""Fixed-target, restore-only crash-recoverable staging socket diagnostic."""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, NamedTuple

DEPLOY_HOST = "hermes-staging-01"
DEPLOY_ROOT = Path("/opt/hermes-compose/staging")
DATA_ROOT = Path("/home/hermes-staging/.hermes-staging")
STATE_ROOT = Path("/var/lib/hermes-staging-diagnostics")
RUNTIME_UID = 1001
RUNTIME_GID = 1001
SUDO_UID = 1002
CONTAINER = "hermes-batumi-staging-gateway"
ENVIRONMENT = "batumi-staging"
DOCKER = "/usr/bin/docker"
MAX_REQUEST_BYTES = 4096
MAX_CONFIG_BYTES = 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
STATE_ORDER = ("PREPARED", "ARMED", "MUTATED", "ENABLED", "OBSERVING", "RESTORING", "RESTORED")
TERMINAL_STATES = {"RESTORED", "RESTORE_FAILED"}
_TRANSITIONS = {a: b for a, b in zip(STATE_ORDER, STATE_ORDER[1:])}
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_EVENT = re.compile(
    r"\[Telegram socket\] event=(response-created|response-closed|response-close-error) "
    r"owner=(general|polling) route=(primary|(?:[0-9]{1,3}\.){3}[0-9]{1,3}) "
    r"local_port=([0-9]{1,5}|unknown)(?:\s|$)"
)


class DiagnosticError(Exception):
    exit_code = 1


class RequestError(DiagnosticError):
    exit_code = 64


class AuthorizationError(DiagnosticError):
    exit_code = 77


class ConfigError(DiagnosticError):
    pass


class ConfigDriftError(ConfigError):
    pass


class StateError(DiagnosticError):
    pass


class TransactionConflictError(StateError):
    pass


class CommandError(DiagnosticError):
    pass


class Request(NamedTuple):
    expected_source_sha: str
    observation_seconds: int
    run_id: str
    run_attempt: str
    nonce: str

    def dictionary(self) -> dict[str, object]:
        return dict(self._asdict())


class ConfigSnapshot(NamedTuple):
    existed: bool
    content: bytes
    mode: int
    uid: int
    gid: int
    sha256: str


class Transaction:
    def __init__(self, path: Path, record: dict[str, object]):
        self.path = path
        self.record = record

    @property
    def state(self) -> str:
        return str(self.record["state"])


class _DuplicateKey(ValueError):
    pass


def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate key: {key}")
        result[key] = value
    return result


def parse_request(stream: BinaryIO) -> Request:
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError("request exceeds 4096 bytes")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise RequestError("request is not UTF-8") from exc
    decoder = json.JSONDecoder(object_pairs_hook=_object_no_duplicates)
    try:
        value, end = decoder.raw_decode(text)
    except _DuplicateKey as exc:
        raise RequestError("request contains duplicate keys") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RequestError("request is not valid JSON") from exc
    if text[end:].strip():
        raise RequestError("request contains trailing data")
    keys = {"expected_source_sha", "observation_seconds", "run_id", "run_attempt", "nonce"}
    if not isinstance(value, dict) or set(value) != keys:
        raise RequestError("request keys do not match the exact schema")
    sha = value["expected_source_sha"]
    duration = value["observation_seconds"]
    run_id = value["run_id"]
    attempt = value["run_attempt"]
    nonce = value["nonce"]
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise RequestError("invalid expected source SHA")
    if type(duration) is not int or duration not in {60, 90, 120}:
        raise RequestError("invalid observation duration")
    if not isinstance(run_id, str) or not _DECIMAL.fullmatch(run_id) or run_id == "0":
        raise RequestError("invalid run ID")
    if not isinstance(attempt, str) or not _DECIMAL.fullmatch(attempt) or attempt == "0":
        raise RequestError("invalid run attempt")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise RequestError("invalid nonce")
    return Request(sha, duration, run_id, attempt, nonce)


def parse_cli(argv: list[str], environ: dict[str, str], euid: int) -> str:
    if argv == ["--recover"]:
        if euid != 0 or "SUDO_UID" in environ:
            raise AuthorizationError("recovery mode requires direct root invocation")
        return "recover"
    if argv:
        raise RequestError("arguments are not permitted")
    return "run"


def authorize_caller(environ: dict[str, str], euid: int) -> None:
    if euid != 0 or environ.get("SUDO_UID") != str(SUDO_UID):
        raise AuthorizationError("caller authorization failed")


def _sanitize(message: object, limit: int = 512) -> str:
    text = str(message)
    text = re.sub(r"[^A-Za-z0-9 _.,:=/@+-]", "?", text)
    return text[:limit]


def render_error(error: BaseException) -> str:
    payload = {"ok": False, "error": error.__class__.__name__, "message": _sanitize(error)}
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return rendered.encode()[:MAX_OUTPUT_BYTES].decode("utf-8", "ignore")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(fd: int) -> None:
    os.fsync(fd)


class ConfigStore:
    """Operate on gateway.json relative to a verified directory descriptor."""

    name = "gateway.json"

    def __init__(self, root: Path, *, expected_uid: int, expected_gid: int):
        self.root = Path(root)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def _open_dir(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.root, flags)
        except OSError as exc:
            raise ConfigError("data directory cannot be opened safely") from exc
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            os.close(fd)
            raise ConfigError("data root is not a directory")
        return fd

    def _read_current(self, dir_fd: int, *, allow_absent: bool) -> ConfigSnapshot:
        # A hostile runtime path may be a FIFO. Open non-blocking so we can
        # inspect and reject its type instead of hanging the privileged helper.
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.name, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            if allow_absent:
                return ConfigSnapshot(False, b"", 0o600, self.expected_uid, self.expected_gid, _sha(b""))
            raise ConfigError("gateway config is absent")
        except OSError as exc:
            raise ConfigError("gateway config cannot be opened safely") from exc
        try:
            st = os.fstat(fd)
            named = os.stat(self.name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(named.st_mode):
                raise ConfigError("gateway config is not regular")
            if (st.st_dev, st.st_ino) != (named.st_dev, named.st_ino) or st.st_nlink != 1:
                raise ConfigError("gateway config identity is unsafe")
            if st.st_size > MAX_CONFIG_BYTES:
                raise ConfigError("gateway config is oversized")
            if (st.st_uid, st.st_gid) != (self.expected_uid, self.expected_gid):
                raise ConfigError("gateway config ownership mismatch")
            content = bytearray()
            while len(content) <= MAX_CONFIG_BYTES:
                chunk = os.read(fd, min(65536, MAX_CONFIG_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > MAX_CONFIG_BYTES:
                raise ConfigError("gateway config is oversized")
            final = os.fstat(fd)
            if (final.st_dev, final.st_ino, final.st_size) != (st.st_dev, st.st_ino, st.st_size):
                raise ConfigError("gateway config changed while reading")
            raw = bytes(content)
            try:
                parsed = json.loads(raw.decode("utf-8", "strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfigError("gateway config is not valid UTF-8 JSON") from exc
            if not isinstance(parsed, dict):
                raise ConfigError("gateway config root is not an object")
            return ConfigSnapshot(True, raw, stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, _sha(raw))
        finally:
            os.close(fd)

    def snapshot(self) -> ConfigSnapshot:
        fd = self._open_dir()
        try:
            return self._read_current(fd, allow_absent=True)
        finally:
            os.close(fd)

    @staticmethod
    def enabled_payload(snapshot: ConfigSnapshot) -> bytes:
        data = json.loads(snapshot.content.decode()) if snapshot.existed else {}
        platforms = data.setdefault("platforms", {})
        if not isinstance(platforms, dict):
            raise ConfigError("platforms is not an object")
        telegram = platforms.setdefault("telegram", {})
        if not isinstance(telegram, dict):
            raise ConfigError("telegram is not an object")
        extra = telegram.setdefault("extra", {})
        if not isinstance(extra, dict):
            raise ConfigError("telegram extra is not an object")
        extra["socket_diagnostics"] = True
        return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()

    def _atomic_write(self, dir_fd: int, content: bytes, mode: int, uid: int, gid: int, tag: str) -> None:
        temp_name = f".{self.name}.{tag}.{os.getpid()}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
        try:
            os.fchown(fd, uid, gid)
            os.fchmod(fd, mode)
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        except BaseException:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        finally:
            os.close(fd)
        os.replace(temp_name, self.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        _fsync_directory(dir_fd)

    def enable(self, snapshot: ConfigSnapshot) -> str:
        content = self.enabled_payload(snapshot)
        fd = self._open_dir()
        try:
            current = self._read_current(fd, allow_absent=True)
            if current.existed != snapshot.existed or current.sha256 != snapshot.sha256:
                raise ConfigDriftError("gateway config drift before mutation")
            self._atomic_write(fd, content, snapshot.mode, snapshot.uid, snapshot.gid, "enable")
            return _sha(content)
        finally:
            os.close(fd)

    def restore(self, snapshot: ConfigSnapshot, expected_mutated_hash: str) -> None:
        fd = self._open_dir()
        try:
            current = self._read_current(fd, allow_absent=True)
            if current.existed == snapshot.existed and current.sha256 == snapshot.sha256:
                return
            if not current.existed or current.sha256 != expected_mutated_hash:
                raise ConfigDriftError("gateway config drift blocks restore")
            if snapshot.existed:
                self._atomic_write(fd, snapshot.content, snapshot.mode, snapshot.uid, snapshot.gid, "restore")
            else:
                os.unlink(self.name, dir_fd=fd)
                _fsync_directory(fd)
        finally:
            os.close(fd)


def _atomic_path_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        view = memoryview(content)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class TransactionStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _read(self, path: Path) -> dict[str, object]:
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError("transaction state is unreadable") from exc
        if not isinstance(value, dict) or value.get("state") not in set(STATE_ORDER) | {"RESTORE_FAILED"}:
            raise StateError("transaction state is invalid")
        return value

    def _write(self, tx: Transaction) -> None:
        _atomic_path_write(tx.path / "state.json", (json.dumps(tx.record, sort_keys=True) + "\n").encode())

    def transactions(self) -> list[Transaction]:
        result = []
        for entry in sorted(self.root.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                raise StateError("unsafe transaction entry")
            state_file = entry / "state.json"
            if state_file.exists():
                result.append(Transaction(entry, self._read(state_file)))
        return result

    def prepare(self, request: Request) -> Transaction:
        identity = f"{request.run_id}-{request.run_attempt}-{request.nonce}"
        digest = _sha(json.dumps(request.dictionary(), sort_keys=True, separators=(",", ":")).encode())
        path = self.root / identity
        if path.exists():
            tx = Transaction(path, self._read(path / "state.json"))
            if tx.record.get("request_digest") != digest:
                raise TransactionConflictError("conflicting transaction replay")
            return tx
        for existing in self.transactions():
            if existing.state not in TERMINAL_STATES:
                raise TransactionConflictError("another transaction is active")
        path.mkdir(mode=0o700)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        record = {"version": 1, "state": "PREPARED", "request": request.dictionary(), "request_digest": digest}
        tx = Transaction(path, record)
        self._write(tx)
        return tx

    def transition(self, tx: Transaction, state_name: str) -> None:
        expected = _TRANSITIONS.get(tx.state)
        if state_name != expected:
            raise StateError("invalid durable state transition")
        tx.record["state"] = state_name
        self._write(tx)

    def fail_restore(self, tx: Transaction) -> None:
        tx.record["state"] = "RESTORE_FAILED"
        self._write(tx)

    def save_snapshot(self, tx: Transaction, snapshot: ConfigSnapshot) -> None:
        if tx.state != "PREPARED":
            raise StateError("snapshot must be saved while prepared")
        if snapshot.existed:
            _atomic_path_write(tx.path / "gateway.json.original", snapshot.content)
        tx.record["snapshot"] = {
            "existed": snapshot.existed, "mode": snapshot.mode, "uid": snapshot.uid,
            "gid": snapshot.gid, "sha256": snapshot.sha256,
        }
        self._write(tx)

    def load_snapshot(self, tx: Transaction) -> ConfigSnapshot:
        value = tx.record.get("snapshot")
        if not isinstance(value, dict):
            raise StateError("transaction snapshot metadata is absent")
        existed = value.get("existed") is True
        content = (tx.path / "gateway.json.original").read_bytes() if existed else b""
        if _sha(content) != value.get("sha256"):
            raise StateError("transaction snapshot hash mismatch")
        return ConfigSnapshot(existed, content, int(value["mode"]), int(value["uid"]), int(value["gid"]), str(value["sha256"]))

    def record_mutated_hash(self, tx: Transaction, digest: str) -> None:
        tx.record["mutated_sha256"] = digest
        self._write(tx)


def command_runner(argv, *, timeout: int, env: dict[str, str], input_data=None, max_output=None) -> str:
    if not argv or not os.path.isabs(argv[0]):
        raise CommandError("command vector is not absolute")
    limit = min(max_output or MAX_COMMAND_OUTPUT_BYTES, MAX_COMMAND_OUTPUT_BYTES)
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                list(argv), stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, input=input_data, timeout=timeout,
                check=False, env=env, close_fds=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise CommandError("bounded command execution failed") from exc
        output.seek(0, io.SEEK_END)
        size = output.tell()
        if size > limit:
            raise CommandError("command output exceeded bound")
        output.seek(0)
        raw = output.read(limit + 1)
    if completed.returncode != 0:
        raise CommandError(f"command failed with status {completed.returncode}")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise CommandError("command output was not UTF-8") from exc


_EFFECTIVE_TRUE = """from gateway.config import Platform, load_gateway_config
value = load_gateway_config().platforms[Platform.TELEGRAM].extra.get("socket_diagnostics")
if value is not True: raise SystemExit("effective value is not literal true")
print("true")
"""
_EFFECTIVE_FALSE = """from gateway.config import Platform, load_gateway_config
value = load_gateway_config().platforms[Platform.TELEGRAM].extra.get("socket_diagnostics")
if value is True: raise SystemExit("effective value is literal true")
print("false")
"""


class DiagnosticExecutor:
    def __init__(self, *, deploy_root=DEPLOY_ROOT, data_root=DATA_ROOT, state_root=STATE_ROOT,
                 expected_uid=RUNTIME_UID, expected_gid=RUNTIME_GID, runner=command_runner,
                 sleep=time.sleep, hostname=lambda: socket.gethostname().split(".")[0]):
        self.deploy_root = Path(deploy_root)
        self.data_root = Path(data_root)
        self.states = TransactionStore(Path(state_root))
        self.config = ConfigStore(Path(data_root), expected_uid=expected_uid, expected_gid=expected_gid)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.runner = runner
        self.sleep = sleep
        self.hostname = hostname

    def _command(self, argv, timeout, max_output=65536):
        return self.runner(tuple(argv), timeout=timeout, env=COMMAND_ENV, input_data=None, max_output=max_output)

    @staticmethod
    def _exact_env(path: Path) -> dict[str, str]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise DiagnosticError("fixed metadata file is unavailable") from exc
        result = {}
        for line in lines:
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or key in result:
                raise DiagnosticError("fixed metadata file is invalid")
            result[key] = value
        return result

    def _preflight(self, request: Request) -> None:
        if self.hostname() != DEPLOY_HOST:
            raise DiagnosticError("host identity mismatch")
        release = self._exact_env(self.deploy_root / "release.env")
        if release != {"HERMES_SOURCE_SHA": request.expected_source_sha, "HERMES_DEPLOY_ENV": ENVIRONMENT}:
            raise DiagnosticError("release metadata mismatch")
        runtime = self._exact_env(self.deploy_root / "runtime.env")
        expected_runtime = {"HERMES_DATA_DIR": str(self.data_root), "HERMES_UID": str(self.expected_uid), "HERMES_GID": str(self.expected_gid)}
        if runtime != expected_runtime:
            raise DiagnosticError("runtime metadata mismatch")
        env_output = self._command((DOCKER, "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", CONTAINER), 15)
        env_lines = env_output.splitlines()
        if env_lines.count("HERMES_SOURCE_SHA=" + request.expected_source_sha) != 1 or env_lines.count("HERMES_DEPLOY_ENV=" + ENVIRONMENT) != 1:
            raise DiagnosticError("container environment mismatch")
        mount = self._command((DOCKER, "inspect", "--format", '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{println .Source}}{{end}}{{end}}', CONTAINER), 15).strip()
        if mount != str(self.data_root):
            raise DiagnosticError("container mount mismatch")
        self._wait_healthy()
        self._effective(False)

    def _wait_healthy(self) -> None:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status = self._command((DOCKER, "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", CONTAINER), 15).strip()
            if status == "healthy":
                return
            self.sleep(3)
        raise CommandError("gateway health deadline expired")

    def _effective(self, enabled: bool) -> None:
        script = _EFFECTIVE_TRUE if enabled else _EFFECTIVE_FALSE
        result = self._command((DOCKER, "exec", "--user", "hermes", CONTAINER, "/usr/bin/python", "-c", script), 60).strip()
        if result != ("true" if enabled else "false"):
            raise CommandError("effective diagnostic verification failed")

    def _restart(self) -> None:
        self._command((DOCKER, "restart", "--time", "90", CONTAINER), 120)
        self._wait_healthy()

    @staticmethod
    def _aggregate(raw: str) -> dict[str, object]:
        counts: dict[tuple[str, str, str], int] = {}
        created: dict[tuple[str, str, str], int] = {}
        terminal: dict[tuple[str, str, str], int] = {}
        for line in raw.splitlines():
            match = _EVENT.search(line)
            if not match:
                continue
            event, owner, route, port = match.groups()
            key = (owner, route, event)
            counts[key] = min(counts.get(key, 0) + 1, 2**31 - 1)
            if port != "unknown":
                port_key = (owner, route, port)
                target = created if event == "response-created" else terminal
                target[port_key] = min(target.get(port_key, 0) + 1, 2**31 - 1)
        if not counts:
            raise DiagnosticError("no socket lifecycle events observed")
        if len(counts) > 256 or len(created) > 4096:
            raise DiagnosticError("diagnostic aggregate exceeds bound")
        return {
            "counts": [{"owner": o, "route": r, "event": e, "count": c} for (o, r, e), c in sorted(counts.items())],
            "created_without_terminal": [
                {"owner": o, "route": r, "local_port": p, "count": c - terminal.get((o, r, p), 0)}
                for (o, r, p), c in sorted(created.items()) if c > terminal.get((o, r, p), 0)
            ],
        }

    def _restore(self, tx: Transaction, snapshot: ConfigSnapshot, mutated_hash: str) -> None:
        if tx.state != "RESTORING":
            while tx.state in {"ARMED", "MUTATED", "ENABLED", "OBSERVING"}:
                next_state = _TRANSITIONS[tx.state]
                if next_state == "RESTORING" or tx.state == "OBSERVING":
                    self.states.transition(tx, "RESTORING")
                    break
                # Recovery skips enable/observe work but records restore intent directly.
                tx.record["state"] = "RESTORING"
                self.states._write(tx)
                break
        self.config.restore(snapshot, mutated_hash)
        last_error = None
        for _ in range(3):
            try:
                self._restart()
                self._effective(False)
                self.states.transition(tx, "RESTORED")
                return
            except DiagnosticError as exc:
                last_error = exc
                self.sleep(3)
        self.states.fail_restore(tx)
        raise DiagnosticError("bounded restore retries exhausted") from last_error

    def run(self, request: Request) -> dict[str, object]:
        lock_path = self.deploy_root / "deploy.lock"
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(lock_path, flags)
        except OSError as exc:
            raise DiagnosticError("deployment lock unavailable") from exc
        try:
            lock_stat = os.fstat(lock_fd)
            named = os.stat(lock_path, follow_symlinks=False)
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1 or (lock_stat.st_dev, lock_stat.st_ino) != (named.st_dev, named.st_ino):
                raise DiagnosticError("deployment lock identity mismatch")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._preflight(request)
            tx = self.states.prepare(request)
            if tx.state == "RESTORED":
                result = tx.record.get("result")
                return result if isinstance(result, dict) else {"replayed": True}
            if tx.state != "PREPARED":
                raise TransactionConflictError("active transaction requires recovery")
            snapshot = self.config.snapshot()
            self.states.save_snapshot(tx, snapshot)
            self.states.transition(tx, "ARMED")
            mutated_hash = self.config.enable(snapshot)
            self.states.record_mutated_hash(tx, mutated_hash)
            self.states.transition(tx, "MUTATED")
            result = None
            try:
                self._restart()
                self.states.transition(tx, "ENABLED")
                self._effective(True)
                self.states.transition(tx, "OBSERVING")
                self.sleep(request.observation_seconds)
                raw = self._command((DOCKER, "logs", "--since", "10m", CONTAINER), 60, MAX_COMMAND_OUTPUT_BYTES)
                result = self._aggregate(raw)
                result["observation_collected"] = True
            finally:
                self._restore(tx, snapshot, mutated_hash)
            tx.record["result"] = result
            self.states._write(tx)
            return result
        finally:
            os.close(lock_fd)

    def recover(self) -> dict[str, int]:
        recovered = 0
        failed = 0
        for tx in self.states.transactions():
            if tx.state in TERMINAL_STATES or tx.state == "PREPARED":
                continue
            try:
                snapshot = self.states.load_snapshot(tx)
                mutated_hash = str(tx.record.get("mutated_sha256") or _sha(self.config.enabled_payload(snapshot)))
                self._restore(tx, snapshot, mutated_hash)
                recovered += 1
            except DiagnosticError:
                if tx.state != "RESTORE_FAILED":
                    self.states.fail_restore(tx)
                failed += 1
        if failed:
            raise DiagnosticError("one or more recovery transactions failed")
        return {"recovered": recovered}


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        mode = parse_cli(argv, dict(os.environ), os.geteuid())
        executor = DiagnosticExecutor()
        if mode == "recover":
            result = executor.recover()
        else:
            authorize_caller(dict(os.environ), os.geteuid())
            request = parse_request(sys.stdin.buffer)
            result = executor.run(request)
        rendered = json.dumps({"ok": True, **result}, sort_keys=True, separators=(",", ":"))
        if len(rendered.encode()) > MAX_OUTPUT_BYTES:
            raise DiagnosticError("sanitized result exceeded output bound")
        print(rendered)
        return 0
    except DiagnosticError as exc:
        print(render_error(exc), file=sys.stderr)
        return exc.exit_code
    except BaseException as exc:
        print(render_error(DiagnosticError("unexpected internal failure")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
