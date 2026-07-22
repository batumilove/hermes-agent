from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
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
    store.restore(snapshot, mutated_hash)
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
    store.restore(snapshot, mutated_hash)
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
        store.restore(snap, mutated_hash)
    assert path.read_text() == '{"operator":"drift"}'


def test_atomic_config_operations_fsync_files_and_directories(diagnostic, tmp_path, monkeypatch):
    root = _config_dir(tmp_path)
    (root / "gateway.json").write_text("{}")
    calls = []
    real_fsync = diagnostic.os.fsync
    monkeypatch.setattr(diagnostic.os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])
    store = diagnostic.ConfigStore(root, expected_uid=os.getuid(), expected_gid=os.getgid())
    snap = store.snapshot()
    mutated_hash = store.enable(snap)
    store.restore(snap, mutated_hash)
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
        self.fail_on = fail_on
        self.restarts = 0

    def __call__(self, argv, *, timeout, env, input_data=None, max_output=None):
        argv = tuple(argv)
        self.calls.append((argv, timeout, dict(env), input_data, max_output))
        if self.fail_on and self.fail_on in argv:
            raise self.d.CommandError("command failed secret=/tmp/private")
        if argv[:2] == ("/usr/bin/docker", "restart"):
            self.restarts += 1
            return ""
        if argv[:2] == ("/usr/bin/docker", "logs"):
            return "[Telegram socket] event=response-created owner=general route=primary local_port=1234\n[Telegram socket] event=response-closed owner=general route=primary local_port=1234\n"
        if argv[:2] == ("/usr/bin/docker", "inspect"):
            fmt = argv[3]
            if "Config.Env" in fmt:
                return "HERMES_SOURCE_SHA=" + "a" * 40 + "\nHERMES_DEPLOY_ENV=batumi-staging\n"
            if "Mounts" in fmt:
                return self.mount_source + "\n"
            if "Health" in fmt:
                return "healthy\n"
            return "1\n"
        if argv[:2] == ("/usr/bin/docker", "exec"):
            script = argv[-1]
            return "true\n" if 'print("true")' in script else "false\n"
        return ""


def _target_tree(tmp_path):
    deploy = tmp_path / "deploy"
    data = tmp_path / "data"
    state = tmp_path / "state"
    deploy.mkdir()
    data.mkdir()
    (deploy / "release.env").write_text("HERMES_SOURCE_SHA=" + "a" * 40 + "\nHERMES_DEPLOY_ENV=batumi-staging\n")
    (deploy / "runtime.env").write_text(f"HERMES_DATA_DIR={data}\nHERMES_UID={os.getuid()}\nHERMES_GID={os.getgid()}\n")
    (deploy / "deploy.lock").touch()
    (data / "gateway.json").write_text("{}")
    return deploy, data, state


def test_executor_uses_fixed_vectors_scrubbed_environment_and_bounded_aggregate(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    fake = FakeRunner(diagnostic, data)
    executor = diagnostic.DiagnosticExecutor(
        deploy_root=deploy,
        data_root=data,
        state_root=state,
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        runner=fake,
        sleep=lambda _: None,
        hostname=lambda: "hermes-staging-01",
    )
    result = executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert result["observation_collected"] is True
    assert result["counts"] == [{"count": 1, "event": "response-closed", "owner": "general", "route": "primary"}, {"count": 1, "event": "response-created", "owner": "general", "route": "primary"}]
    assert fake.restarts == 2  # one enable; one restore
    assert all(call[2] == diagnostic.COMMAND_ENV for call in fake.calls)
    assert all(isinstance(call[0], tuple) and call[0][0].startswith("/") for call in fake.calls)
    assert not any("shell" in str(call).lower() for call in fake.calls)
    assert json.loads((data / "gateway.json").read_text()).get("platforms") is None


@pytest.mark.parametrize("fail_on", ["restart", "logs", "exec"])
def test_every_runtime_failure_converges_to_restore_only(diagnostic, tmp_path, fail_on):
    deploy, data, state = _target_tree(tmp_path)
    original = (data / "gateway.json").read_bytes()
    fake = FakeRunner(diagnostic, data, fail_on=fail_on)
    executor = diagnostic.DiagnosticExecutor(
        deploy_root=deploy, data_root=data, state_root=state,
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
    mutated_hash = config.enable(snap)
    store.record_mutated_hash(tx, mutated_hash)
    store.transition(tx, "MUTATED")
    for state_name in ["ENABLED", "OBSERVING", "RESTORING"]:
        if diagnostic.STATE_ORDER.index(state_name) <= diagnostic.STATE_ORDER.index(crash_state):
            store.transition(tx, state_name)
    fake = FakeRunner(diagnostic, data)
    executor = diagnostic.DiagnosticExecutor(
        deploy_root=deploy, data_root=data, state_root=state,
        expected_uid=os.getuid(), expected_gid=os.getgid(), runner=fake,
        sleep=lambda _: None, hostname=lambda: "hermes-staging-01",
    )
    result = executor.recover()
    assert result["recovered"] == 1
    assert (data / "gateway.json").read_bytes() == snap.content
    assert json.loads((tx.path / "state.json").read_text())["state"] == "RESTORED"
    assert not any(call[0][:2] == ("/usr/bin/docker", "logs") for call in fake.calls)


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
    assert "--recover" in service and "WantedBy=" not in service
    assert "OnBootSec=" in timer and "OnUnitActiveSec=" in timer
    assert "--stage" in installer and "--authorize" in installer
    assert "visudo -c -f" in installer
    assert "usermod" not in installer and "gpasswd" not in installer
    assert "docker group" in installer.lower() and "containment remains fail" in installer.lower()
    assert "systemctl enable" not in installer and "systemctl start" not in installer


def test_helper_and_installer_do_not_use_shell_execution(diagnostic):
    source = HELPER.read_text()
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "subprocess.Popen" not in source
    assert diagnostic.MAX_COMMAND_OUTPUT_BYTES <= 2 * 1024 * 1024
    assert diagnostic.MAX_OUTPUT_BYTES <= 16 * 1024
