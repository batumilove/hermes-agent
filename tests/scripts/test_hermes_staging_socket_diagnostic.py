from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts/staging/hermes_staging_socket_diagnostic.py"


def _load():
    spec = importlib.util.spec_from_file_location("staging_diagnostic", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def diagnostic():
    return _load()


def request(**changes):
    value = {
        "expected_source_sha": "a" * 40,
        "observation_seconds": 60,
        "run_id": "12345",
        "run_attempt": "2",
        "nonce": "nonce_0123456789abcdef",
    }
    value.update(changes)
    return json.dumps(value).encode()


def test_fixed_security_constants_and_no_argv_contract(diagnostic):
    assert diagnostic.DEPLOY_HOST == "hermes-staging-01"
    assert diagnostic.DEPLOY_ROOT == Path("/opt/hermes-compose/staging")
    assert diagnostic.DATA_ROOT == Path("/home/hermes-staging/.hermes-staging")
    assert diagnostic.RUNTIME_UID == diagnostic.RUNTIME_GID == 1001
    assert diagnostic.SUDO_UID == 1002
    assert diagnostic.CONTAINER == "hermes-batumi-staging-gateway"
    assert diagnostic.ENVIRONMENT == "batumi-staging"
    assert diagnostic.STATE_ROOT == Path("/var/lib/hermes-staging-diagnostics")
    with pytest.raises(diagnostic.RequestError, match="arguments"):
        diagnostic.parse_cli(["unexpected"], {}, 0)
    assert diagnostic.parse_cli([], {"SUDO_UID": "1002"}, 0) == "run"
    assert diagnostic.parse_cli(["--recover"], {}, 0) == "recover"
    with pytest.raises(diagnostic.AuthorizationError):
        diagnostic.parse_cli(["--recover"], {"SUDO_UID": "1002"}, 0)


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"{", "JSON"),
        (b"{} trailing", "trailing"),
        (b'{' + b'"expected_source_sha":"' + b'a' * 40 + b'","expected_source_sha":"' + b'a' * 40 + b'"}', "duplicate"),
        (b"\xff", "UTF-8"),
        (b" " * 4097, "4096"),
        (json.dumps({"unknown": 1}).encode(), "keys"),
    ],
)
def test_request_rejects_malformed_bounded_input(diagnostic, payload, message):
    with pytest.raises(diagnostic.RequestError, match=message):
        diagnostic.parse_request(io.BytesIO(payload))


@pytest.mark.parametrize(
    "change",
    [
        {"expected_source_sha": "A" * 40},
        {"expected_source_sha": "a" * 39},
        {"observation_seconds": 61},
        {"observation_seconds": "60"},
        {"run_id": "01"},
        {"run_id": "-1"},
        {"run_attempt": 1},
        {"nonce": "short"},
        {"nonce": "bad nonce 0123456789"},
    ],
)
def test_request_enforces_exact_types_and_allowlists(diagnostic, change):
    with pytest.raises(diagnostic.RequestError):
        diagnostic.parse_request(io.BytesIO(request(**change)))


def test_request_accepts_only_exact_schema_and_authorized_sudo_uid(diagnostic):
    parsed = diagnostic.parse_request(io.BytesIO(request()))
    assert parsed.observation_seconds == 60
    diagnostic.authorize_caller({"SUDO_UID": "1002"}, 0)
    for env, euid in [({}, 0), ({"SUDO_UID": "1001"}, 0), ({"SUDO_UID": "1002"}, 1002)]:
        with pytest.raises(diagnostic.AuthorizationError):
            diagnostic.authorize_caller(env, euid)


def test_errors_are_sanitized_and_output_is_bounded(diagnostic):
    error = diagnostic.DiagnosticError("bad\nsecret\x00" + "x" * 5000)
    rendered = diagnostic.render_error(error)
    assert "\n" not in rendered and "\x00" not in rendered
    assert len(rendered.encode()) <= diagnostic.MAX_OUTPUT_BYTES


def _config_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    return root


def _restore(store, snapshot, mutated_hash, tmp_path: Path, guard_name=None):
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir(mode=0o700, exist_ok=True)
    quarantine.chmod(0o700)
    fd = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if guard_name is None:
            store.restore(snapshot, mutated_hash, fd)
        else:
            store.restore(snapshot, mutated_hash, fd, guard_name)
    finally:
        os.close(fd)


def test_regular_config_snapshot_enable_and_exact_restore(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    original = b'{"platforms":{"telegram":{"extra":{"keep":"yes"}}}}\n'
    path.write_bytes(original)
    path.chmod(0o640)
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    mutated_hash = store.enable(snapshot)
    assert json.loads(path.read_bytes())["platforms"]["telegram"]["extra"]["socket_diagnostics"] is True
    _restore(store, snapshot, mutated_hash, tmp_path)
    st = path.stat()
    assert path.read_bytes() == original
    assert stat.S_IMODE(st.st_mode) == 0o640
    assert (st.st_uid, st.st_gid) == (os.getuid(), os.getgid())


def test_absent_config_is_removed_on_restore(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    assert snapshot.existed is False
    mutated_hash = store.enable(snapshot)
    assert (root / "gateway.json").is_file()
    _restore(store, snapshot, mutated_hash, tmp_path)
    assert not (root / "gateway.json").exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "oversize", "array"])
def test_snapshot_rejects_unsafe_or_invalid_config(diagnostic, tmp_path, kind):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    if kind == "symlink":
        target = root / "target"
        target.write_text("{}")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "hardlink":
        target = root / "target"
        target.write_text("{}")
        os.link(target, path)
    elif kind == "oversize":
        path.write_bytes(b" " * (diagnostic.MAX_CONFIG_BYTES + 1))
    else:
        path.write_text("[]")
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    with pytest.raises(diagnostic.ConfigError):
        store.snapshot()


def test_restore_refuses_compare_and_swap_drift(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    path.write_text("{}")
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snap = store.snapshot()
    mutated_hash = store.enable(snap)
    path.write_text('{"operator":"drift"}')
    with pytest.raises(diagnostic.ConfigDriftError):
        _restore(store, snap, mutated_hash, tmp_path)
    assert path.read_text() == '{"operator":"drift"}'


def test_enable_guarded_cas_preserves_drift_at_rename_boundary(diagnostic, tmp_path, monkeypatch):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    path.write_text("{}")
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    real_exchange = diagnostic._rename_exchange
    fired = False

    def race(first_fd, first_name, second_fd, second_name):
        nonlocal fired
        if not fired:
            fired = True
            path.write_text('{"operator":"enable-drift"}')
        return real_exchange(first_fd, first_name, second_fd, second_name)

    monkeypatch.setattr(diagnostic, "_rename_exchange", race)
    with pytest.raises(diagnostic.ConfigDriftError):
        store.enable(snapshot)
    assert json.loads(path.read_text()) == {"operator": "enable-drift"}


def test_restore_guarded_cas_preserves_drift_at_rename_boundary(diagnostic, tmp_path, monkeypatch):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    path.write_text("{}")
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    mutated_hash = store.enable(snapshot)
    real_exchange = diagnostic._rename_exchange
    fired = False

    def race(first_fd, first_name, second_fd, second_name):
        nonlocal fired
        if not fired:
            fired = True
            path.write_text('{"operator":"restore-drift"}')
        return real_exchange(first_fd, first_name, second_fd, second_name)

    monkeypatch.setattr(diagnostic, "_rename_exchange", race)
    with pytest.raises(diagnostic.ConfigDriftError):
        _restore(store, snapshot, mutated_hash, tmp_path)
    assert json.loads(path.read_text()) == {"operator": "restore-drift"}


@pytest.mark.parametrize("phase", ["enable", "restore"])
def test_atomic_exchange_process_death_is_recoverable(diagnostic, tmp_path, phase):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    original = b'{"operator":"original"}\n'
    path.write_bytes(original)
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    mutated_hash = store.enable(snapshot)
    if phase == "enable":
        _restore(store, snapshot, mutated_hash, tmp_path)

    pid = os.fork()
    if pid == 0:
        real_exchange = diagnostic._rename_exchange

        def die_after_exchange(first_fd, first_name, second_fd, second_name):
            real_exchange(first_fd, first_name, second_fd, second_name)
            os._exit(73)

        diagnostic._rename_exchange = die_after_exchange
        if phase == "enable":
            store.enable(snapshot)
        else:
            _restore(store, snapshot, mutated_hash, tmp_path)
        os._exit(74)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 73

    _restore(store, snapshot, mutated_hash, tmp_path)
    assert path.read_bytes() == original
    assert list(root.glob(".gateway.json.tx-*.swap")) == []


def test_guard_replacement_at_cleanup_is_preserved(diagnostic, tmp_path, monkeypatch):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    path.write_text('{"original":true}')
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    mutated_hash = store.enable(snapshot)
    real_unlink = diagnostic.os.unlink
    fired = False

    def replace_then_unlink(name, *, dir_fd=None):
        nonlocal fired
        if not fired and name.endswith(".swap"):
            fired = True
            replacement = root / ".replacement"
            replacement.write_text('{"concurrent":"preserved"}')
            os.replace(replacement, root / name)
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(diagnostic.os, "unlink", replace_then_unlink)
    with pytest.raises(diagnostic.ConfigDriftError, match="concurrent"):
        _restore(store, snapshot, mutated_hash, tmp_path)
    guards = list(root.glob(".gateway.json.tx-*.swap"))
    assert len(guards) == 1
    assert json.loads(guards[0].read_text()) == {"concurrent": "preserved"}
    assert path.read_bytes() == snapshot.content


def test_process_death_after_guard_quarantine_is_recoverable(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    original = b'{"original":true}\n'
    path.write_bytes(original)
    transactions = diagnostic.TransactionStore(tmp_path / "state")
    tx = transactions.prepare(diagnostic.parse_request(io.BytesIO(request())))
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    transactions.save_snapshot(tx, snapshot)
    guard_name = transactions.load_guard_name(tx)
    mutated_hash = store.enable(snapshot, guard_name)

    pid = os.fork()
    if pid == 0:
        real_rename = diagnostic._rename_noreplace

        def die_after_quarantine(old_fd, old_name, new_fd, new_name):
            real_rename(old_fd, old_name, new_fd, new_name)
            if old_name.endswith(".swap") and old_fd != new_fd:
                os._exit(75)

        diagnostic._rename_noreplace = die_after_quarantine
        quarantine_fd = transactions.open_transaction(tx)
        store.restore(snapshot, mutated_hash, quarantine_fd, guard_name)
        os._exit(76)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 75
    assert (tx.path / guard_name).is_file()
    assert not list(root.glob(".hermes-staging-diagnostic-quarantine*"))

    quarantine_fd = transactions.open_transaction(tx)
    try:
        store.restore(snapshot, mutated_hash, quarantine_fd, guard_name)
    finally:
        os.close(quarantine_fd)
    assert path.read_bytes() == original
    assert not (tx.path / guard_name).exists()


def test_orphaned_command_retains_shared_lock_until_exit(diagnostic, tmp_path):
    lock_path = tmp_path / "shared.lock"
    lock_path.touch()
    ready = tmp_path / "ready"
    helper_pid = os.fork()
    if helper_pid == 0:
        lock_fd = os.open(lock_path, os.O_RDWR)
        diagnostic.fcntl.flock(lock_fd, diagnostic.fcntl.LOCK_EX)
        diagnostic.command_runner(
            (
                sys.executable,
                "-c",
                "import pathlib,time,sys;pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(1)",
                str(ready),
            ),
            timeout=5,
            env=diagnostic.COMMAND_ENV,
            lock_fd=lock_fd,
        )
        os._exit(0)
    deadline = time.monotonic() + 2
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    os.kill(helper_pid, diagnostic.signal.SIGKILL)
    os.waitpid(helper_pid, 0)
    probe = os.open(lock_path, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            diagnostic.fcntl.flock(probe, diagnostic.fcntl.LOCK_EX | diagnostic.fcntl.LOCK_NB)
        time.sleep(1.1)
        diagnostic.fcntl.flock(probe, diagnostic.fcntl.LOCK_EX | diagnostic.fcntl.LOCK_NB)
    finally:
        os.close(probe)


def test_data_root_requires_exact_owner_and_mode(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    root.chmod(0o777)
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    with pytest.raises(diagnostic.ConfigError, match="ownership or mode"):
        store.snapshot()


def test_atomic_config_operations_fsync_files_and_directories(diagnostic, tmp_path, monkeypatch):
    root = _config_dir(tmp_path)
    (root / "gateway.json").write_text("{}")
    calls = []
    real_fsync = diagnostic.os.fsync
    monkeypatch.setattr(diagnostic.os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snap = store.snapshot()
    mutated_hash = store.enable(snap)
    _restore(store, snap, mutated_hash, tmp_path)
    assert len(calls) >= 4


def test_state_machine_durable_transitions_replay_and_single_active(diagnostic, tmp_path):
    states = diagnostic.TransactionStore(tmp_path / "state")
    req = diagnostic.parse_request(io.BytesIO(request()))
    tx = states.prepare(req)
    assert tx.state == "PREPARED"
    for state in ["ARMED", "MUTATED", "ENABLED", "OBSERVING", "RESTORING", "RESTORED"]:
        states.transition(tx, state)
        assert json.loads((tx.path / "state.json").read_text())["state"] == state
    assert states.prepare(req).state == "RESTORED"
    conflicting = diagnostic.parse_request(io.BytesIO(request(observation_seconds=90)))
    with pytest.raises(diagnostic.TransactionConflictError):
        states.prepare(conflicting)
    other = diagnostic.parse_request(io.BytesIO(request(run_id="54321", nonce="nonce_abcdef0123456789")))
    active = states.prepare(other)
    states.transition(active, "ARMED")
    third = diagnostic.parse_request(io.BytesIO(request(run_id="999", nonce="nonce_9999999999999999")))
    with pytest.raises(diagnostic.TransactionConflictError):
        states.prepare(third)


def test_invalid_transition_fails_closed(diagnostic, tmp_path):
    states = diagnostic.TransactionStore(tmp_path / "state")
    tx = states.prepare(diagnostic.parse_request(io.BytesIO(request())))
    with pytest.raises(diagnostic.StateError):
        states.transition(tx, "ENABLED")


class FakeRunner:
    def __init__(self, diagnostic, mount_source, fail_on=None):
        self.d = diagnostic
        self.mount_source = str(mount_source)
        self.calls = []
        self.timeline = []
        self.fail_on = fail_on
        self.restarts = 0

    def __call__(self, argv, *, timeout, env, input_data=None, max_output=None, lock_fd=None):
        argv = tuple(argv)
        self.timeline.append(argv[1])
        self.calls.append((argv, timeout, dict(env), input_data, max_output, lock_fd))
        if self.fail_on and self.fail_on in argv:
            raise self.d.CommandError("command failed secret=/tmp/private")
        if argv[:2] == ("/usr/bin/docker", "restart"):
            self.restarts += 1
            return ""
        if argv[:2] == ("/usr/bin/docker", "logs"):
            return "[Telegram socket] event=response-created owner=general route=primary local_port=1234\n[Telegram socket] event=response-closed owner=general route=primary local_port=1234\n"
        if argv[:2] == ("/usr/bin/docker", "inspect"):
            fmt = argv[3]
            if ".Id" in fmt and ".State.StartedAt" in fmt:
                return "b" * 64 + " 2026-07-22T20:00:00.123456789Z\n"
            if "Config.Env" in fmt:
                return (
                    "HERMES_SOURCE_SHA=" + "a" * 40
                    + "\nHERMES_DEPLOY_ENV=batumi-staging\nHERMES_HOME=/opt/data\n"
                )
            if "Config.Image" in fmt:
                return "ghcr.io/batumilove/hermes-agent-deploy@sha256:" + "1" * 64 + "\n"
            if "Mounts" in fmt:
                return self.mount_source + "\n"
            if "Health" in fmt:
                return "healthy\n"
            return "1\n"
        if argv[:2] == ("/usr/bin/docker", "exec"):
            script = argv[-1]
            return "true\n" if 'print("true")' in script else "false\n"
        return ""


def test_effective_check_uses_the_container_application_venv(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    fake = FakeRunner(diagnostic, data)
    executor = _executor(diagnostic, tmp_path, deploy, data, state, fake)
    executor._start_deadline(60)

    executor._effective(False)

    call = fake.calls[-1][0]
    assert call == (
        "/usr/bin/docker", "exec", "--env", "HERMES_HOME=/opt/data",
        "--user", "hermes", diagnostic.CONTAINER,
        "/opt/hermes/.venv/bin/python", "-c", diagnostic._EFFECTIVE_FALSE,
    )


def test_loader_observes_temporary_gateway_json_fallback_without_changing_primary_yaml(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    config_yaml = root / "config.yaml"
    config_yaml.write_bytes(b"_config_version: 1\n")
    config_yaml.chmod(0o640)
    before = config_yaml.read_bytes(), config_yaml.stat()
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()

    env = {
        "HOME": str(tmp_path),
        "HERMES_HOME": str(root),
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO),
    }
    probe = (
        "from gateway.config import Platform,load_gateway_config;"
        "p=load_gateway_config().platforms.get(Platform.TELEGRAM);"
        "v=None if p is None else p.extra.get('socket_diagnostics');"
        "print('true' if v is True else 'false')"
    )

    assert subprocess.check_output([sys.executable, "-c", probe], env=env, text=True).strip() == "false"
    mutated_hash = store.enable(snapshot)
    assert subprocess.check_output([sys.executable, "-c", probe], env=env, text=True).strip() == "true"
    _restore(store, snapshot, mutated_hash, tmp_path)
    assert subprocess.check_output([sys.executable, "-c", probe], env=env, text=True).strip() == "false"
    assert not (root / "gateway.json").exists()

    after = config_yaml.stat()
    assert config_yaml.read_bytes() == before[0]
    assert (
        after.st_dev, after.st_ino, after.st_nlink, after.st_mode,
        after.st_uid, after.st_gid, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ) == (
        before[1].st_dev, before[1].st_ino, before[1].st_nlink, before[1].st_mode,
        before[1].st_uid, before[1].st_gid, before[1].st_size, before[1].st_mtime_ns,
        before[1].st_ctime_ns,
    )


def test_primary_yaml_socket_diagnostic_setting_overrides_gateway_json_fallback(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    (root / "config.yaml").write_text(
        "platforms:\n  telegram:\n    extra:\n      socket_diagnostics: false\n"
    )
    (root / "gateway.json").write_text(
        '{"platforms":{"telegram":{"extra":{"socket_diagnostics":true}}}}\n'
    )
    env = {
        "HOME": str(tmp_path),
        "HERMES_HOME": str(root),
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO),
    }
    probe = (
        "from gateway.config import Platform,load_gateway_config;"
        "p=load_gateway_config().platforms.get(Platform.TELEGRAM);"
        "print('true' if p and p.extra.get('socket_diagnostics') is True else 'false')"
    )
    assert subprocess.check_output([sys.executable, "-c", probe], env=env, text=True).strip() == "false"


def test_effective_false_accepts_an_absent_telegram_platform(diagnostic, monkeypatch, capsys):
    gateway = types.ModuleType("gateway")
    gateway.__path__ = []
    config = types.ModuleType("gateway.config")
    setattr(config, "Platform", types.SimpleNamespace(TELEGRAM="telegram"))
    setattr(config, "load_gateway_config", lambda: types.SimpleNamespace(platforms={}))
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.config", config)

    exec(diagnostic._EFFECTIVE_FALSE, {})

    assert capsys.readouterr().out == "false\n"


def test_effective_true_fails_generically_when_telegram_platform_is_absent(diagnostic, monkeypatch):
    gateway = types.ModuleType("gateway")
    gateway.__path__ = []
    config = types.ModuleType("gateway.config")
    setattr(config, "Platform", types.SimpleNamespace(TELEGRAM="telegram"))
    setattr(config, "load_gateway_config", lambda: types.SimpleNamespace(platforms={}))
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.config", config)

    with pytest.raises(SystemExit, match="effective value is not literal true"):
        exec(diagnostic._EFFECTIVE_TRUE, {})


def _target_tree(tmp_path):
    deploy = tmp_path / "deploy"
    data = tmp_path / "data"
    state = tmp_path / "state"
    deploy.mkdir()
    data.mkdir()
    data.chmod(0o700)
    (deploy / "release.env").write_text(
        "HERMES_IMAGE=ghcr.io/batumilove/hermes-agent-deploy@sha256:" + "1" * 64
        + "\nHERMES_DEPLOY_ENV=batumi-staging\nHERMES_SOURCE_SHA=" + "a" * 40 + "\n"
    )
    (deploy / "runtime.env").write_text(f"HERMES_DATA_DIR={data}\nHERMES_UID={os.getuid()}\nHERMES_GID={os.getgid()}\n")
    (deploy / "release.env").chmod(0o600)
    (deploy / "runtime.env").chmod(0o600)
    (deploy / "deploy.lock").touch()
    (tmp_path / "shared.lock").touch()
    (tmp_path / "shared.lock").chmod(0o660)
    (data / "gateway.json").write_text("{}")
    return deploy, data, state


class _ObservedCrash(BaseException):
    pass


class _RecordingCrashBarrier:
    def __init__(self, target, runner):
        self.target = target
        self.runner = runner
        self.observations = []
        self.triggered = False

    def after_transition(self, tx, state_name):
        self.observations.append((state_name, tuple(self.runner.timeline)))
        if state_name == self.target and not self.triggered:
            self.triggered = True
            raise _ObservedCrash


def _write_crash_token(path, diagnostic, tx, target, helper_path, **overrides):
    payload = {
        "version": 1,
        "transaction": tx.path.name,
        "target": target,
        "helper_sha256": diagnostic._sha(helper_path.read_bytes()),
        "expected_source_sha": tx.record["request"]["expected_source_sha"],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)


def test_crash_barrier_defaults_to_canonical_installed_helper(diagnostic):
    assert diagnostic.CrashBarrier().helper_path == Path("/usr/local/libexec/hermes-staging-diagnostic")
    assert diagnostic.CrashBarrier().helper_path != Path(diagnostic.__file__)


def test_crash_barrier_consumes_exact_token_before_signalling(diagnostic, tmp_path):
    states = diagnostic.TransactionStore(tmp_path / "state")
    tx = states.prepare(diagnostic.parse_request(io.BytesIO(request())))
    helper_path = tmp_path / "helper"
    helper_path.write_bytes(b"reviewed-helper")
    helper_path.chmod(0o755)
    token_path = tmp_path / "crash-token.json"
    _write_crash_token(token_path, diagnostic, tx, "ARMED", helper_path)
    signals = []

    def signal_process(pid, sig):
        record = json.loads((tx.path / "state.json").read_text())
        signals.append((pid, sig, record["state"], record["crash_barrier"], token_path.exists()))
        raise _ObservedCrash

    barrier = diagnostic.CrashBarrier(
        token_path=token_path,
        helper_path=helper_path,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        pid=lambda: 4242,
        signal_process=signal_process,
    )
    states.transition(tx, "ARMED")

    with pytest.raises(_ObservedCrash):
        barrier.after_transition(tx, "ARMED")

    assert signals == [(4242, diagnostic.signal.SIGKILL, "ARMED", {
        "target": "ARMED", "helper_sha256": diagnostic._sha(helper_path.read_bytes()),
        "expected_source_sha": "a" * 40, "consumed": True,
    }, False)]
    assert not token_path.exists()


def test_crash_barrier_unlink_failure_does_not_record_consumption(diagnostic, tmp_path, monkeypatch):
    states = diagnostic.TransactionStore(tmp_path / "state")
    tx = states.prepare(diagnostic.parse_request(io.BytesIO(request())))
    helper_path = tmp_path / "helper"
    helper_path.write_bytes(b"reviewed-helper")
    helper_path.chmod(0o755)
    token_path = tmp_path / "crash-token.json"
    _write_crash_token(token_path, diagnostic, tx, "ARMED", helper_path)
    real_unlink = diagnostic.os.unlink

    def fail_token_unlink(path, *args, **kwargs):
        if path == token_path.name:
            raise OSError("injected unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(diagnostic.os, "unlink", fail_token_unlink)
    barrier = diagnostic.CrashBarrier(
        token_path=token_path, helper_path=helper_path,
        expected_uid=os.getuid(), expected_gid=os.getgid(),
        signal_process=lambda *_: pytest.fail("failed consumption must not signal"),
    )
    states.transition(tx, "ARMED")

    with pytest.raises(OSError, match="injected unlink failure"):
        barrier.after_transition(tx, "ARMED")

    record = json.loads((tx.path / "state.json").read_text())
    assert record["state"] == "ARMED"
    assert "crash_barrier" not in record
    assert token_path.exists()


def test_crash_barrier_leaves_valid_later_target_armed(diagnostic, tmp_path):
    states = diagnostic.TransactionStore(tmp_path / "state")
    tx = states.prepare(diagnostic.parse_request(io.BytesIO(request())))
    helper_path = tmp_path / "helper"
    helper_path.write_bytes(b"reviewed-helper")
    helper_path.chmod(0o755)
    token_path = tmp_path / "crash-token.json"
    _write_crash_token(token_path, diagnostic, tx, "MUTATED", helper_path)
    barrier = diagnostic.CrashBarrier(
        token_path=token_path, helper_path=helper_path, expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        signal_process=lambda *_: pytest.fail("must not signal before the target state"),
    )

    barrier.after_transition(tx, "ARMED")

    assert token_path.exists()


@pytest.mark.parametrize("defect", ["mode", "helper-digest", "source-binding", "schema"])
def test_crash_barrier_rejects_unsafe_or_malformed_token(diagnostic, tmp_path, defect):
    states = diagnostic.TransactionStore(tmp_path / "state")
    tx = states.prepare(diagnostic.parse_request(io.BytesIO(request())))
    helper_path = tmp_path / "helper"
    helper_path.write_bytes(b"reviewed-helper")
    helper_path.chmod(0o755)
    token_path = tmp_path / "crash-token.json"
    overrides = {"helper_sha256": "0" * 64} if defect == "helper-digest" else {}
    if defect == "source-binding":
        overrides["expected_source_sha"] = "b" * 40
    _write_crash_token(token_path, diagnostic, tx, "ARMED", helper_path, **overrides)
    if defect == "mode":
        token_path.chmod(0o644)
    elif defect == "schema":
        token_path.write_text('{"version":1,"version":1}\n')
        token_path.chmod(0o600)
    barrier = diagnostic.CrashBarrier(
        token_path=token_path, helper_path=helper_path, expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        signal_process=lambda *_: pytest.fail("unsafe token must not signal"),
    )

    with pytest.raises(diagnostic.StateError, match="crash token"):
        barrier.after_transition(tx, "ARMED")

    assert token_path.exists()


@pytest.mark.parametrize(
    "target,expected_stops,expected_restarts,expected_execs",
    [
        ("ARMED", 0, 0, 1),
        ("MUTATED", 1, 0, 1),
        ("ENABLED", 1, 1, 1),
        ("OBSERVING", 1, 1, 2),
        ("RESTORING", 2, 1, 2),
    ],
)
def test_crash_barrier_runs_immediately_after_durable_state_and_before_next_command(
    diagnostic, tmp_path, monkeypatch, target, expected_stops, expected_restarts, expected_execs
):
    deploy, data, state = _target_tree(tmp_path)
    fake = FakeRunner(diagnostic, data)
    barrier = _RecordingCrashBarrier(target, fake)
    executor = _executor(
        diagnostic, tmp_path, deploy, data, state, fake, crash_barrier=barrier,
    )
    monkeypatch.setattr(executor, "_observation_since", lambda: "2026-07-22T20:00:00.000000Z")

    with pytest.raises(_ObservedCrash):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))

    state_name, timeline = next(item for item in barrier.observations if item[0] == target)
    assert state_name == target
    assert timeline.count("stop") == expected_stops
    assert timeline.count("restart") == expected_restarts
    assert timeline.count("exec") == expected_execs


def test_recovery_never_invokes_forward_crash_barrier(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    store = diagnostic.TransactionStore(state)
    req = diagnostic.parse_request(io.BytesIO(request()))
    tx = store.prepare(req)
    config = diagnostic.ConfigStore(data, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = config.snapshot()
    store.save_snapshot(tx, snapshot)
    store.transition(tx, "ARMED")
    mutated_hash = config.enable(snapshot, store.load_guard_name(tx))
    store.record_mutated_hash(tx, mutated_hash)
    store.transition(tx, "MUTATED")
    fake = FakeRunner(diagnostic, data)
    barrier = _RecordingCrashBarrier("RESTORING", fake)
    executor = _executor(diagnostic, tmp_path, deploy, data, state, fake, crash_barrier=barrier)

    assert executor.recover()["recovered"] == 1
    assert barrier.observations == []


def test_executor_uses_fixed_vectors_scrubbed_environment_and_bounded_aggregate(diagnostic, tmp_path, monkeypatch):
    deploy, data, state = _target_tree(tmp_path)
    fake = FakeRunner(diagnostic, data)
    executor = diagnostic.DiagnosticExecutor(
        deploy_root=deploy,
        data_root=data,
        state_root=state, lock_path=tmp_path / "shared.lock",
        lock_uid=os.getuid(),
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        runner=fake,
        sleep=lambda _: None,
        hostname=lambda: "hermes-staging-01",
    )
    monkeypatch.setattr(executor, "_observation_since", lambda: (fake.timeline.append("observation_since"), "2026-07-22T20:00:00.000000Z")[1])
    result = executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert result["observation_collected"] is True
    assert result["counts"] == [{"count": 1, "event": "response-closed", "owner": "general", "route": "primary"}, {"count": 1, "event": "response-created", "owner": "general", "route": "primary"}]
    assert fake.restarts == 1  # enable restarts; restore starts the explicitly stopped container
    assert [call[0][1] for call in fake.calls if call[0][:2] == ("/usr/bin/docker", "start")] == ["start"]
    assert all(call[2] == diagnostic.COMMAND_ENV for call in fake.calls)
    assert all(call[5] is not None for call in fake.calls)
    assert all(isinstance(call[0], tuple) and call[0][0].startswith("/") for call in fake.calls)
    assert not any("shell" in str(call).lower() for call in fake.calls)
    log_call = next(call for call in fake.calls if call[0][:2] == ("/usr/bin/docker", "logs"))
    assert log_call[0][2] == "--since" and log_call[0][3].endswith("Z")
    assert "10m" not in log_call[0]
    first_restart = fake.timeline.index("restart")
    observation_since = fake.timeline.index("observation_since")
    observation_stop = fake.timeline.index("stop", first_restart + 1)
    logs = fake.timeline.index("logs")
    assert observation_since < first_restart < observation_stop < logs
    assert json.loads((data / "gateway.json").read_text()).get("platforms") is None


def test_cross_filesystem_quarantine_aborts_before_mutation(diagnostic, tmp_path, monkeypatch):
    deploy, data, state = _target_tree(tmp_path)
    original = (data / "gateway.json").read_bytes()
    fake = FakeRunner(diagnostic, data)
    executor = _executor(diagnostic, tmp_path, deploy, data, state, fake)
    real_device = executor.config.device()
    monkeypatch.setattr(executor.config, "device", lambda: real_device + 1)
    with pytest.raises(diagnostic.StateError, match="different filesystems"):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert (data / "gateway.json").read_bytes() == original
    assert fake.restarts == 0
    journal = next(state.glob("*/state.json"))
    assert json.loads(journal.read_text())["state"] == "ABORTED"


@pytest.mark.parametrize("fail_on", ["restart", "logs", "exec"])
def test_every_runtime_failure_converges_to_restore_only(diagnostic, tmp_path, fail_on):
    deploy, data, state = _target_tree(tmp_path)
    original = (data / "gateway.json").read_bytes()
    fake = FakeRunner(diagnostic, data, fail_on=fail_on)
    executor = diagnostic.DiagnosticExecutor(
        deploy_root=deploy, data_root=data, state_root=state, lock_path=tmp_path / "shared.lock",
        lock_uid=os.getuid(),
        expected_uid=os.getuid(), expected_gid=os.getgid(), runner=fake,
        sleep=lambda _: None, hostname=lambda: "hermes-staging-01",
    )
    with pytest.raises(diagnostic.DiagnosticError):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert (data / "gateway.json").read_bytes() == original
    journals = list(state.glob("*/state.json"))
    if journals:
        assert json.loads(journals[0].read_text())["state"] in {"RESTORED", "RESTORE_FAILED"}


@pytest.mark.parametrize("crash_state", ["ARMED", "MUTATED", "ENABLED", "OBSERVING", "RESTORING"])
def test_recovery_after_each_durable_mutation_state_is_restore_only(diagnostic, tmp_path, crash_state):
    deploy, data, state = _target_tree(tmp_path)
    store = diagnostic.TransactionStore(state)
    req = diagnostic.parse_request(io.BytesIO(request()))
    tx = store.prepare(req)
    config = diagnostic.ConfigStore(data, expected_uid=os.getuid(), expected_gid=os.getgid())
    snap = config.snapshot()
    store.save_snapshot(tx, snap)
    store.transition(tx, "ARMED")
    if crash_state != "ARMED":
        mutated_hash = config.enable(snap, store.load_guard_name(tx))
        store.record_mutated_hash(tx, mutated_hash)
        store.transition(tx, "MUTATED")
        for state_name in ["ENABLED", "OBSERVING", "RESTORING"]:
            if diagnostic.STATE_ORDER.index(state_name) <= diagnostic.STATE_ORDER.index(crash_state):
                store.transition(tx, state_name)
    fake = FakeRunner(diagnostic, data)
    executor = diagnostic.DiagnosticExecutor(
        deploy_root=deploy, data_root=data, state_root=state, lock_path=tmp_path / "shared.lock",
        lock_uid=os.getuid(),
        expected_uid=os.getuid(), expected_gid=os.getgid(), runner=fake,
        sleep=lambda _: None, hostname=lambda: "hermes-staging-01",
    )
    result = executor.recover()
    assert result["recovered"] == 1
    assert (data / "gateway.json").read_bytes() == snap.content
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"
    assert not any(call[0][:2] == ("/usr/bin/docker", "logs") for call in fake.calls)


def test_restore_starts_the_container_after_an_explicit_stop(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    store = diagnostic.TransactionStore(state)
    request_value = diagnostic.parse_request(io.BytesIO(request()))
    tx = store.prepare(request_value)
    config = diagnostic.ConfigStore(data, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = config.snapshot()
    store.save_snapshot(tx, snapshot)
    store.transition(tx, "ARMED")
    mutated_hash = config.enable(snapshot, store.load_guard_name(tx))
    store.record_mutated_hash(tx, mutated_hash)
    store.transition(tx, "MUTATED")
    fake = FakeRunner(diagnostic, data)
    executor = _executor(diagnostic, tmp_path, deploy, data, state, fake)

    assert executor.recover()["recovered"] == 1

    runtime_actions = [
        call[0][1]
        for call in fake.calls
        if call[0][0] == "/usr/bin/docker" and call[0][1] in {"stop", "start", "restart"}
    ]
    assert runtime_actions == ["stop", "start"]


def test_exact_restored_config_with_unhealthy_runtime_still_stops_then_starts(
    diagnostic, tmp_path, monkeypatch
):
    deploy, data, state = _target_tree(tmp_path)
    tx, snapshot = _armed_transaction(diagnostic, state, data, mutate=True)
    config = diagnostic.ConfigStore(data, expected_uid=os.getuid(), expected_gid=os.getgid())
    mutated_hash = diagnostic._sha((data / "gateway.json").read_bytes())
    quarantine_fd = diagnostic.TransactionStore(state).open_transaction(tx)
    try:
        config.restore(
            snapshot,
            mutated_hash,
            quarantine_fd,
            diagnostic.TransactionStore(state).load_guard_name(tx),
        )
    finally:
        os.close(quarantine_fd)
    assert config.matches(snapshot)

    fake = FakeRunner(diagnostic, data)
    executor = _executor(diagnostic, tmp_path, deploy, data, state, fake)
    health_states = iter(["unhealthy", "healthy"])
    monkeypatch.setattr(executor, "_health_status", lambda: next(health_states, "healthy"))

    assert executor.recover() == {"recovered": 1, "aborted": 0}
    runtime_actions = [
        call[0][1]
        for call in fake.calls
        if call[0][0] == "/usr/bin/docker" and call[0][1] in {"stop", "start", "restart"}
    ]
    assert runtime_actions == ["stop", "start"]
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"


@pytest.mark.parametrize("precheck", ["matches", "health"])
def test_restore_precheck_failure_is_durably_restore_failed(
    diagnostic, tmp_path, monkeypatch, precheck
):
    deploy, data, state = _target_tree(tmp_path)
    tx, snapshot = _armed_transaction(diagnostic, state, data, mutate=True)
    mutated_hash = diagnostic._sha((data / "gateway.json").read_bytes())
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    if precheck == "matches":
        monkeypatch.setattr(
            executor.config,
            "matches",
            lambda _snapshot: (_ for _ in ()).throw(diagnostic.ConfigDriftError("precheck drift")),
        )
    else:
        monkeypatch.setattr(executor.config, "matches", lambda _snapshot: True)
        monkeypatch.setattr(
            executor,
            "_health_status",
            lambda: (_ for _ in ()).throw(diagnostic.CommandError("health inspect failed")),
        )

    with pytest.raises(diagnostic.DiagnosticError, match="bounded restore retries exhausted"):
        executor._restore(tx, snapshot, mutated_hash)

    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORE_FAILED"
    assert not any(
        call[0][0] == "/usr/bin/docker" and call[0][1] in {"stop", "start", "restart"}
        for call in executor.runner.calls
    )


def test_installation_artifacts_are_dormant_exact_and_warn_about_containment():
    sudoers = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic.sudoers").read_text()
    service = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.service").read_text()
    timer = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.timer").read_text()
    installer = (REPO / "scripts/deploy/install-staging-diagnostic-helper.sh").read_text()
    assert "SETENV" not in sudoers
    assert 'sha256:__HELPER_SHA256__ /usr/local/libexec/hermes-staging-diagnostic ""' in sudoers
    assert "NOPASSWD:" in sudoers
    assert "--recover" not in sudoers
    assert "User=root" in service and "NetworkNamespacePath=" not in service
    assert "IPAddressDeny=any" in service
    # Recovery atomically quarantines a guard from DATA_ROOT (/home) into
    # TRANSACTION_ROOT (/var). ProtectSystem=strict plus per-path
    # ReadWritePaths makes those locations separate bind mounts and turns the
    # required renameat2 into EXDEV even when both paths share st_dev.
    assert "ProtectSystem=full" in service
    assert "ProtectHome=false" in service
    assert "ReadWritePaths=" not in service
    assert "ReadOnlyPaths=/etc /opt/hermes-compose/staging" in service
    assert "--recover" in service and "WantedBy=" not in service
    assert "OnBootSec=" in timer and "OnUnitActiveSec=" in timer
    assert "--stage" in installer and "--authorize" in installer
    assert "/usr/local/sbin/hermes-staging-diagnostic-installer" in installer
    assert "run only the externally verified root-owned installer copy" in installer
    assert "installed installer digest mismatch" in installer
    assert "visudo -c -f" in installer
    assert "usermod" not in installer and "gpasswd" not in installer
    assert "docker group" in installer.lower() and "containment remains fail" in installer.lower()
    assert "systemctl enable" not in installer and "systemctl start" not in installer


def test_helper_output_bounds_are_fixed(diagnostic):
    assert diagnostic.MAX_COMMAND_OUTPUT_BYTES <= 2 * 1024 * 1024
    assert diagnostic.MAX_OUTPUT_BYTES <= 16 * 1024


def test_command_runner_terminates_immediately_at_output_bound(diagnostic):
    started = time.monotonic()
    with pytest.raises(diagnostic.CommandError, match="output exceeded bound"):
        diagnostic.command_runner(
            ("/usr/bin/python3", "-c", "import os,time; os.write(1,b'x'*(3*1024*1024)); time.sleep(10)"),
            timeout=15, env=diagnostic.COMMAND_ENV, max_output=1024,
        )
    assert time.monotonic() - started < 5


def test_installer_layout_keeps_manifest_outside_transaction_root(diagnostic, tmp_path):
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "artifact-manifest.json").write_text("{}")
    states = diagnostic.TransactionStore(state_root / "transactions")
    tx = states.prepare(diagnostic.parse_request(io.BytesIO(request())))
    assert tx.path.parent == state_root / "transactions"
    assert diagnostic.TRANSACTION_ROOT == diagnostic.STATE_ROOT / "transactions"


def _executor(diagnostic, tmp_path, deploy, data, state, runner=None, **kwargs):
    return diagnostic.DiagnosticExecutor(
        deploy_root=deploy,
        data_root=data,
        state_root=state,
        lock_path=tmp_path / "shared.lock",
        lock_uid=os.getuid(),
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        runner=runner or FakeRunner(diagnostic, data),
        sleep=kwargs.pop("sleep", lambda _: None),
        hostname=lambda: "hermes-staging-01",
        **kwargs,
    )


def test_release_metadata_uses_real_three_field_format_and_pinned_container_image(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    executor._start_deadline(diagnostic.FORWARD_DEADLINE_SECONDS)
    executor._preflight(diagnostic.parse_request(io.BytesIO(request())))

    (deploy / "release.env").write_text(
        "HERMES_IMAGE=ghcr.io/batumilove/hermes-agent-deploy:latest\n"
        "HERMES_DEPLOY_ENV=batumi-staging\nHERMES_SOURCE_SHA=" + "a" * 40 + "\n"
    )
    with pytest.raises(diagnostic.DiagnosticError, match="release metadata"):
        executor._preflight(diagnostic.parse_request(io.BytesIO(request())))


@pytest.mark.parametrize("name,kind", [("release.env", "symlink"), ("runtime.env", "fifo"), ("release.env", "oversize")])
def test_metadata_reads_are_descriptor_relative_bounded_and_nofollow(diagnostic, tmp_path, name, kind):
    deploy, data, state = _target_tree(tmp_path)
    path = deploy / name
    path.unlink()
    if kind == "symlink":
        target = tmp_path / "outside"
        target.write_text("HERMES_SOURCE_SHA=" + "a" * 40)
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"x" * (diagnostic.MAX_METADATA_BYTES + 1))
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    started = time.monotonic()
    with pytest.raises(diagnostic.DiagnosticError):
        executor._preflight(diagnostic.parse_request(io.BytesIO(request())))
    assert time.monotonic() - started < 2


def test_enable_and_restore_cas_include_mode_uid_gid(diagnostic, tmp_path):
    root = _config_dir(tmp_path)
    path = root / "gateway.json"
    path.write_text("{}")
    path.chmod(0o640)
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = store.snapshot()
    path.chmod(0o600)
    with pytest.raises(diagnostic.ConfigDriftError):
        store.enable(snapshot)

    path.chmod(0o640)
    mutated_hash = store.enable(snapshot)
    path.chmod(0o600)
    with pytest.raises(diagnostic.ConfigDriftError):
        _restore(store, snapshot, mutated_hash, tmp_path)


def test_preflight_rejects_disk_true_even_when_runtime_false(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    (data / "gateway.json").write_text(
        '{"platforms":{"telegram":{"extra":{"socket_diagnostics":true}}}}'
    )
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    executor._start_deadline(diagnostic.FORWARD_DEADLINE_SECONDS)
    with pytest.raises(diagnostic.ConfigError, match="on-disk"):
        executor._preflight(diagnostic.parse_request(io.BytesIO(request())))


def test_preflight_requires_container_root_hermes_home(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)

    class MissingRootHome(FakeRunner):
        def __call__(self, argv, **kwargs):
            if tuple(argv[:2]) == ("/usr/bin/docker", "inspect") and "Config.Env" in argv[3]:
                return "HERMES_SOURCE_SHA=" + "a" * 40 + "\nHERMES_DEPLOY_ENV=batumi-staging\n"
            return super().__call__(argv, **kwargs)

    executor = _executor(diagnostic, tmp_path, deploy, data, state, MissingRootHome(diagnostic, data))
    executor._start_deadline(diagnostic.FORWARD_DEADLINE_SECONDS)
    with pytest.raises(diagnostic.DiagnosticError, match="container environment"):
        executor._preflight(diagnostic.parse_request(io.BytesIO(request())))


@pytest.mark.parametrize(
    ("key", "conflict"),
    [
        ("HERMES_HOME", "/wrong"),
        ("HERMES_SOURCE_SHA", "b" * 40),
        ("HERMES_DEPLOY_ENV", "wrong-staging"),
    ],
)
def test_preflight_rejects_duplicate_conflicting_bound_environment(
    diagnostic, tmp_path, key, conflict
):
    deploy, data, state = _target_tree(tmp_path)

    class ConflictingEnvironment(FakeRunner):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if tuple(argv[:2]) == ("/usr/bin/docker", "inspect") and "Config.Env" in argv[3]:
                return result + f"{key}={conflict}\n"
            return result

    executor = _executor(
        diagnostic, tmp_path, deploy, data, state, ConflictingEnvironment(diagnostic, data)
    )
    executor._start_deadline(diagnostic.FORWARD_DEADLINE_SECONDS)
    with pytest.raises(diagnostic.DiagnosticError, match="container environment"):
        executor._preflight(diagnostic.parse_request(io.BytesIO(request())))


def _armed_transaction(diagnostic, state, data, *, mutate=False):
    store = diagnostic.TransactionStore(state)
    tx = store.prepare(diagnostic.parse_request(io.BytesIO(request())))
    config = diagnostic.ConfigStore(data, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = config.snapshot()
    store.save_snapshot(tx, snapshot)
    store.transition(tx, "ARMED")
    if mutate:
        config.enable(snapshot, store.load_guard_name(tx))
    return tx, snapshot


def test_true_armed_before_mutation_recovers_exactly(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    tx, snapshot = _armed_transaction(diagnostic, state, data, mutate=False)
    result = _executor(diagnostic, tmp_path, deploy, data, state).recover()
    assert result == {"recovered": 1, "aborted": 0}
    assert (data / "gateway.json").read_bytes() == snapshot.content
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"


def test_replacement_before_mutated_hash_journal_recovers(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    tx, snapshot = _armed_transaction(diagnostic, state, data, mutate=True)
    assert "mutated_sha256" not in tx.record
    _executor(diagnostic, tmp_path, deploy, data, state).recover()
    assert (data / "gateway.json").read_bytes() == snapshot.content
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"


def test_prepared_recovery_aborts_under_lock_and_unblocks_future_run(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    store = diagnostic.TransactionStore(state)
    tx = store.prepare(diagnostic.parse_request(io.BytesIO(request())))
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    assert executor.recover() == {"recovered": 0, "aborted": 1}
    assert json.loads((tx.path / "state.json").read_text())["state"] == "ABORTED"
    other = diagnostic.parse_request(io.BytesIO(request(run_id="9876", nonce="nonce_9876543210123456")))
    assert executor.run(other)["observation_collected"] is True


def test_restore_failed_remains_blocking_and_retries_until_verified(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    tx, _ = _armed_transaction(diagnostic, state, data, mutate=True)
    failing = FakeRunner(diagnostic, data, fail_on="start")
    executor = _executor(diagnostic, tmp_path, deploy, data, state, failing)
    with pytest.raises(diagnostic.DiagnosticError):
        executor.recover()
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORE_FAILED"
    other = diagnostic.parse_request(io.BytesIO(request(run_id="9876", nonce="nonce_9876543210123456")))
    with pytest.raises(diagnostic.TransactionConflictError):
        executor.run(other)

    succeeding = FakeRunner(diagnostic, data)
    assert _executor(diagnostic, tmp_path, deploy, data, state, succeeding).recover()["recovered"] == 1
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"
    assert succeeding.timeline.count("stop") == 0
    assert succeeding.restarts == 0
    assert any(call[0][:2] == ("/usr/bin/docker", "exec") for call in succeeding.calls)


def test_original_absent_executor_recovery_removes_config(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    (data / "gateway.json").unlink()
    tx, snapshot = _armed_transaction(diagnostic, state, data, mutate=True)
    assert snapshot.existed is False
    _executor(diagnostic, tmp_path, deploy, data, state).recover()
    assert not (data / "gateway.json").exists()
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"


def test_recovery_waits_for_same_shared_lock(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    _armed_transaction(diagnostic, state, data, mutate=True)
    lock_fd = os.open(tmp_path / "shared.lock", os.O_RDWR)
    diagnostic.fcntl.flock(lock_fd, diagnostic.fcntl.LOCK_EX)
    executor = _executor(diagnostic, tmp_path, deploy, data, state, sleep=time.sleep)
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(executor.recover()), daemon=True)
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive() and outcome == []
    diagnostic.fcntl.flock(lock_fd, diagnostic.fcntl.LOCK_UN)
    os.close(lock_fd)
    worker.join(timeout=2)
    assert outcome == [{"recovered": 1, "aborted": 0}]


def test_restore_guard_starts_at_armed_and_handles_mutation_unknown(diagnostic, tmp_path, monkeypatch):
    deploy, data, state = _target_tree(tmp_path)
    original = (data / "gateway.json").read_bytes()
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    real_enable = executor.config.enable

    def replace_then_fail(snapshot, guard_name):
        real_enable(snapshot, guard_name)
        raise OSError("journal-adjacent simulated failure")

    monkeypatch.setattr(executor.config, "enable", replace_then_fail)
    with pytest.raises(OSError):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert (data / "gateway.json").read_bytes() == original
    journal = next(state.glob("*/state.json"))
    assert json.loads(journal.read_text())["state"] == "RESTORED"


def test_global_deadline_and_service_timeout_fit_workflow(diagnostic):
    assert diagnostic.TOTAL_DEADLINE_SECONDS == (
        diagnostic.FORWARD_DEADLINE_SECONDS + diagnostic.RESTORE_DEADLINE_SECONDS
    )
    assert 0 < diagnostic.TOTAL_DEADLINE_SECONDS < 20 * 60
    assert diagnostic.RESTORE_DEADLINE_SECONDS >= 240
    service = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.service").read_text()
    assert f"TimeoutStartSec={diagnostic.RESTORE_DEADLINE_SECONDS + 30}" in service


def test_forward_failure_starts_fresh_restore_reserve(diagnostic, tmp_path, monkeypatch):
    deploy, data, state = _target_tree(tmp_path)
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    reserves = []

    monkeypatch.setattr(
        executor.config,
        "enable",
        lambda _snapshot, _guard_name: (_ for _ in ()).throw(OSError("forward failure")),
    )
    monkeypatch.setattr(
        executor,
        "_restore",
        lambda _tx, _snapshot, _digest, *, allow_crash=False: reserves.append(
            (executor.deadline - time.monotonic(), allow_crash)
        ),
    )
    with pytest.raises(OSError, match="forward failure"):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert len(reserves) == 1
    reserve, allow_crash = reserves[0]
    assert diagnostic.RESTORE_DEADLINE_SECONDS - 1 < reserve <= diagnostic.RESTORE_DEADLINE_SECONDS
    assert allow_crash is True


@pytest.mark.parametrize("raw", [
    "[Telegram socket] event=response-created owner=general route=primary local_port=1234\n",
    "[Telegram socket] event=response-closed owner=general route=primary local_port=1234\n",
    "[Telegram socket] event=response-closed owner=general route=primary local_port=1234\n"
    "[Telegram socket] event=response-created owner=general route=primary local_port=1234\n",
    "[Telegram socket] event=response-created owner=general route=primary local_port=unknown\n",
    "[Telegram socket] event=response-created owner=general route=primary local_port=unknown\n"
    "[Telegram socket] event=response-closed owner=general route=primary local_port=unknown\n",
])
def test_incomplete_socket_lifecycle_fails_canary(diagnostic, raw):
    with pytest.raises(diagnostic.DiagnosticError, match="incomplete"):
        diagnostic.DiagnosticExecutor._aggregate(raw)


def test_balanced_pre_response_lifecycle_is_aggregated(diagnostic):
    raw = (
        "[Telegram socket] event=socket-opened owner=general route=primary local_port=1234\n"
        "[Telegram socket] event=socket-closed owner=general route=primary local_port=1234\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert result == {
        "counts": [
            {"count": 1, "event": "socket-closed", "owner": "general", "route": "primary"},
            {"count": 1, "event": "socket-opened", "owner": "general", "route": "primary"},
        ],
        "created_without_terminal": [],
    }


def test_default_gateway_warning_prefix_is_aggregated(diagnostic):
    prefix = "WARNING plugins.platforms.telegram.telegram_network: "
    raw = (
        prefix + "[Telegram socket] event=socket-opened owner=general route=primary local_port=1234\n"
        + prefix + "[Telegram socket] event=socket-closed owner=general route=primary local_port=1234\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert {item["event"] for item in result["counts"]} == {"socket-opened", "socket-closed"}


def test_interleaved_socket_and_response_lifecycles_balance_separately(diagnostic):
    raw = (
        "[Telegram socket] event=socket-opened owner=general route=primary local_port=1234\n"
        "[Telegram socket] event=response-created owner=general route=primary local_port=1234\n"
        "[Telegram socket] event=socket-closed owner=general route=primary local_port=1234\n"
        "[Telegram socket] event=response-closed owner=general route=primary local_port=1234\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert {item["event"] for item in result["counts"]} == {
        "socket-opened", "socket-closed", "response-created", "response-closed",
    }
    assert result["created_without_terminal"] == []


def test_correlated_runtime_lifecycle_schema_is_aggregated(diagnostic):
    raw = (
        "[Telegram socket] event=request-started owner=polling route=primary request_id=5 local_port=none\n"
        "[Telegram socket] event=socket-opened owner=polling route=primary request_id=5 local_port=43626\n"
        "[Telegram socket] event=response-created owner=polling route=primary request_id=5 local_port=43626\n"
        "[Telegram socket] event=socket-close-started owner=polling route=primary request_id=5 local_port=43626\n"
        "[Telegram socket] event=socket-closed owner=polling route=primary request_id=5 local_port=43626\n"
        "[Telegram socket] event=response-closed owner=polling route=primary request_id=5 local_port=43626\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert {item["event"] for item in result["counts"]} == {
        "request-started",
        "socket-opened",
        "response-created",
        "socket-close-started",
        "socket-closed",
        "response-closed",
    }
    assert result["created_without_terminal"] == []


def test_correlated_response_close_error_then_success_balances_once(diagnostic):
    raw = (
        "[Telegram socket] event=request-started owner=general route=primary request_id=42 local_port=none\n"
        "[Telegram socket] event=socket-opened owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=response-created owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=socket-close-started owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=socket-closed owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=response-close-error owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=response-closed owner=general route=primary request_id=42 local_port=43210\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert {item["event"] for item in result["counts"]} == {
        "request-started",
        "socket-opened",
        "response-created",
        "socket-close-started",
        "socket-closed",
        "response-close-error",
        "response-closed",
    }
    assert result["created_without_terminal"] == []


def test_correlated_response_close_error_without_success_fails_canary(diagnostic):
    raw = (
        "[Telegram socket] event=request-started owner=general route=primary request_id=42 local_port=none\n"
        "[Telegram socket] event=socket-opened owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=response-created owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=socket-close-started owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=socket-closed owner=general route=primary request_id=42 local_port=43210\n"
        "[Telegram socket] event=response-close-error owner=general route=primary request_id=42 local_port=43210\n"
    )
    with pytest.raises(diagnostic.DiagnosticError, match="incomplete"):
        diagnostic.DiagnosticExecutor._aggregate(raw)


def test_correlated_cancelled_request_balances_socket_without_response(diagnostic):
    raw = (
        "[Telegram socket] event=request-started owner=polling route=primary request_id=19 local_port=none\n"
        "[Telegram socket] event=socket-opened owner=polling route=primary request_id=19 local_port=56840\n"
        "[Telegram socket] event=socket-close-started owner=polling route=primary request_id=19 local_port=56840\n"
        "[Telegram socket] event=socket-closed owner=polling route=primary request_id=19 local_port=56840\n"
        "[Telegram socket] event=request-cancelled owner=polling route=primary request_id=19 local_port=none\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert {item["event"] for item in result["counts"]} == {
        "request-started",
        "socket-opened",
        "socket-close-started",
        "socket-closed",
        "request-cancelled",
    }
    assert result["created_without_terminal"] == []


def test_correlated_failed_request_record_is_aggregated(diagnostic):
    raw = (
        "[Telegram socket] event=request-started owner=general route=primary request_id=21 local_port=none\n"
        "[Telegram socket] event=socket-opened owner=general route=primary request_id=21 local_port=56841\n"
        "[Telegram socket] event=socket-close-started owner=general route=primary request_id=21 local_port=56841\n"
        "[Telegram socket] event=socket-closed owner=general route=primary request_id=21 local_port=56841\n"
        "[Telegram socket] event=request-failed owner=general route=primary request_id=21 local_port=none\n"
    )
    result = diagnostic.DiagnosticExecutor._aggregate(raw)
    assert {item["event"] for item in result["counts"]} == {
        "request-started",
        "socket-opened",
        "socket-close-started",
        "socket-closed",
        "request-failed",
    }
    assert result["created_without_terminal"] == []


def test_correlated_terminal_for_different_request_fails_canary(diagnostic):
    raw = (
        "[Telegram socket] event=request-started owner=polling route=primary request_id=19 local_port=none\n"
        "[Telegram socket] event=socket-opened owner=polling route=primary request_id=19 local_port=56840\n"
        "[Telegram socket] event=socket-closed owner=polling route=primary request_id=20 local_port=56840\n"
    )
    with pytest.raises(diagnostic.DiagnosticError, match="incomplete"):
        diagnostic.DiagnosticExecutor._aggregate(raw)


@pytest.mark.parametrize("line", [
    "[Telegram socket] event=request-started owner=general route=primary local_port=none",
    "[Telegram socket] event=request-started owner=general route=primary request_id=1 local_port=1234",
    "[Telegram socket] event=socket-opened owner=general route=primary request_id=0 local_port=1234",
    "[Telegram socket] event=socket-opened owner=general route=primary request_id=none local_port=1234",
    "[Telegram socket] event=socket-opened owner=general route=primary request_id=1 local_port=none",
    "[Telegram socket] event=request-cancelled owner=general route=primary request_id=1 local_port=1234",
    "[Telegram socket] event=request-failed owner=general route=primary request_id=1 local_port=1234",
])
def test_invalid_correlated_lifecycle_record_fails_closed(diagnostic, line):
    with pytest.raises(diagnostic.DiagnosticError, match="malformed"):
        diagnostic.DiagnosticExecutor._aggregate(line)


@pytest.mark.parametrize("raw", [
    "[Telegram socket] event=socket-opened owner=general route=primary local_port=1234\n"
    "[Telegram socket] event=response-created owner=general route=primary local_port=5678\n"
    "[Telegram socket] event=response-closed owner=general route=primary local_port=5678\n",
    "[Telegram socket] event=socket-opened owner=general route=primary local_port=1234\n"
    "[Telegram socket] event=socket-closed owner=general route=primary local_port=1234\n"
    "[Telegram socket] event=socket-closed owner=general route=primary local_port=1234\n"
    "[Telegram socket] event=response-created owner=general route=primary local_port=5678\n"
    "[Telegram socket] event=response-closed owner=general route=primary local_port=5678\n",
])
def test_incomplete_pre_response_lifecycle_fails_canary(diagnostic, raw):
    with pytest.raises(diagnostic.DiagnosticError, match="incomplete"):
        diagnostic.DiagnosticExecutor._aggregate(raw)


@pytest.mark.parametrize("line", [
    "[Telegram socket] event=socket-unknown owner=general route=primary local_port=1234",
    "[Telegram socket] event=socket-opened owner=general route=primary local_port=1234 extra=forbidden",
])
def test_malformed_socket_lifecycle_record_fails_closed(diagnostic, line):
    with pytest.raises(diagnostic.DiagnosticError, match="malformed"):
        diagnostic.DiagnosticExecutor._aggregate(line)


@pytest.mark.parametrize("malformed", [
    "https://127.0.0.1/path?token=forbidden [Telegram socket] event=socket-opened owner=general route=primary local_port=1234",
    "WARNING [TOKEN_CANARY_123] plugins.platforms.telegram.telegram_network: [Telegram socket] event=socket-opened owner=general route=primary local_port=1234",
    "[Telegram socket] event=bogus owner=general route=primary local_port=1234 [Telegram socket] event=socket-opened owner=general route=primary local_port=1234",
    "[Telegram socket] owner=general event=socket-opened route=primary local_port=1234",
    "[Telegram socket] extra=forbidden event=socket-opened owner=general route=primary local_port=1234",
])
def test_any_malformed_lifecycle_line_fails_even_with_valid_pair(diagnostic, malformed):
    valid = (
        "[Telegram socket] event=socket-opened owner=general route=primary local_port=5678\n"
        "[Telegram socket] event=socket-closed owner=general route=primary local_port=5678\n"
    )
    with pytest.raises(diagnostic.DiagnosticError, match="malformed"):
        diagnostic.DiagnosticExecutor._aggregate(malformed + "\n" + valid)


@pytest.mark.parametrize("field", [
    "route=127.0.0.1 local_port=1234",
    "route=999.999.999.999 local_port=1234",
    "route=primary local_port=0",
    "route=primary local_port=65536",
    "route=primary local_port=99999",
])
def test_producer_impossible_route_or_port_fails_closed(diagnostic, field):
    raw = (
        f"[Telegram socket] event=socket-opened owner=general {field}\n"
        f"[Telegram socket] event=socket-closed owner=general {field}\n"
    )
    with pytest.raises(diagnostic.DiagnosticError, match="malformed"):
        diagnostic.DiagnosticExecutor._aggregate(raw)


def test_container_identity_change_during_logs_fails_and_restores(diagnostic, tmp_path, monkeypatch):
    deploy, data, state = _target_tree(tmp_path)
    original = (data / "gateway.json").read_bytes()
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    identities = iter(["stable", "stable", "stable", "changed"])
    monkeypatch.setattr(executor, "_container_identity", lambda: next(identities))
    with pytest.raises(diagnostic.DiagnosticError, match="log collection"):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert (data / "gateway.json").read_bytes() == original


def test_installer_manifest_binds_all_root_owned_artifacts_and_shared_lock():
    installer = (REPO / "scripts/deploy/install-staging-diagnostic-helper.sh").read_text()
    deployer = (REPO / "scripts/deploy/hermes-compose-deploy.sh").read_text()
    tmpfiles = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic.tmpfiles").read_text()
    service = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.service").read_text()
    for value in ("reviewed_commit", "reviewed_tree", "installer", "helper", "sudoers", "service", "timer", "tmpfiles", "lock"):
        assert value in installer
    assert "status --porcelain" not in installer
    assert "cat-file blob" in installer
    assert "artifact-manifest.json" in installer
    assert 'staged_root="$state_root/staged"' in installer
    assert 'staged_sudoers="$staged_root/hermes-staging-diagnostic.sudoers"' in installer
    assert "/run/lock/hermes-staging-diagnostic.lock" in installer
    assert "/run/lock/hermes-staging-diagnostic.lock" in deployer
    assert "stat -c '%U:%G:%a:%h:%s'" in deployer
    assert "root:hermes-deploy:660:1:0" in deployer
    assert "/usr/bin/git --no-replace-objects -c safe.directory=\"$repo_root\"" in installer
    assert "systemd-tmpfiles --create" in installer
    assert "f /run/lock/hermes-staging-diagnostic.lock 0660 root hermes-deploy -" in tmpfiles
    assert "systemd-tmpfiles-setup.service" in service
    assert '""' in (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic.sudoers").read_text()


def test_workflow_ssh_uses_only_pinned_identity_and_trust_store():
    workflow = (REPO / ".github/workflows/staging-telegram-socket-diagnostics.yml").read_text()
    for option in (
        "-F /dev/null",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        'UserKnownHostsFile=$HOME/.ssh/known_hosts',
        "GlobalKnownHostsFile=/dev/null",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "ChallengeResponseAuthentication=no",
        "PreferredAuthentications=publickey",
        "NumberOfPasswordPrompts=0",
    ):
        assert option in workflow
