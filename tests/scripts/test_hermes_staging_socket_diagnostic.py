from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
import threading
import time
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


def _target_tree(tmp_path):
    deploy = tmp_path / "deploy"
    data = tmp_path / "data"
    state = tmp_path / "state"
    deploy.mkdir()
    data.mkdir()
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


def test_executor_uses_fixed_vectors_scrubbed_environment_and_bounded_aggregate(diagnostic, tmp_path):
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
    result = executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert result["observation_collected"] is True
    assert result["counts"] == [{"count": 1, "event": "response-closed", "owner": "general", "route": "primary"}, {"count": 1, "event": "response-created", "owner": "general", "route": "primary"}]
    assert fake.restarts == 2  # one enable; one restore
    assert all(call[2] == diagnostic.COMMAND_ENV for call in fake.calls)
    assert all(isinstance(call[0], tuple) and call[0][0].startswith("/") for call in fake.calls)
    assert not any("shell" in str(call).lower() for call in fake.calls)
    log_call = next(call for call in fake.calls if call[0][:2] == ("/usr/bin/docker", "logs"))
    assert log_call[0][2] == "--since" and log_call[0][3].isdigit()
    assert "10m" not in log_call[0]
    assert json.loads((data / "gateway.json").read_text()).get("platforms") is None


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
    mutated_hash = config.enable(snap)
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
        store.restore(snapshot, mutated_hash)


def test_preflight_rejects_disk_true_even_when_runtime_false(diagnostic, tmp_path):
    deploy, data, state = _target_tree(tmp_path)
    (data / "gateway.json").write_text(
        '{"platforms":{"telegram":{"extra":{"socket_diagnostics":true}}}}'
    )
    executor = _executor(diagnostic, tmp_path, deploy, data, state)
    with pytest.raises(diagnostic.ConfigError, match="on-disk"):
        executor._preflight(diagnostic.parse_request(io.BytesIO(request())))


def _armed_transaction(diagnostic, state, data, *, mutate=False):
    store = diagnostic.TransactionStore(state)
    tx = store.prepare(diagnostic.parse_request(io.BytesIO(request())))
    config = diagnostic.ConfigStore(data, expected_uid=os.getuid(), expected_gid=os.getgid())
    snapshot = config.snapshot()
    store.save_snapshot(tx, snapshot)
    store.transition(tx, "ARMED")
    if mutate:
        config.enable(snapshot)
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
    failing = FakeRunner(diagnostic, data, fail_on="restart")
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
    assert succeeding.restarts >= 1


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

    def replace_then_fail(snapshot):
        real_enable(snapshot)
        raise OSError("journal-adjacent simulated failure")

    monkeypatch.setattr(executor.config, "enable", replace_then_fail)
    with pytest.raises(OSError):
        executor.run(diagnostic.parse_request(io.BytesIO(request())))
    assert (data / "gateway.json").read_bytes() == original
    journal = next(state.glob("*/state.json"))
    assert json.loads(journal.read_text())["state"] == "RESTORED"


def test_global_deadline_and_service_timeout_fit_workflow(diagnostic):
    assert 0 < diagnostic.TOTAL_DEADLINE_SECONDS < 20 * 60
    service = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.service").read_text()
    assert f"TimeoutStartSec={diagnostic.TOTAL_DEADLINE_SECONDS + 30}" in service


def test_installer_manifest_binds_all_root_owned_artifacts_and_shared_lock():
    installer = (REPO / "scripts/deploy/install-staging-diagnostic-helper.sh").read_text()
    deployer = (REPO / "scripts/deploy/hermes-compose-deploy.sh").read_text()
    tmpfiles = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic.tmpfiles").read_text()
    service = (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.service").read_text()
    for value in ("reviewed_commit", "reviewed_tree", "helper", "sudoers", "service", "timer", "tmpfiles", "lock"):
        assert value in installer
    assert "status --porcelain --untracked-files=all" in installer
    assert "artifact-manifest.json" in installer
    assert "staged/hermes-staging-diagnostic.sudoers" in installer
    assert "/run/lock/hermes-staging-diagnostic.lock" in installer
    assert "/run/lock/hermes-staging-diagnostic.lock" in deployer
    assert "git -C \"$repo_root\" show \"$reviewed_commit:$repository_path\"" in installer
    assert "systemd-tmpfiles --create" in installer
    assert "f /run/lock/hermes-staging-diagnostic.lock 0660 root hermes-deploy -" in tmpfiles
    assert "systemd-tmpfiles-setup.service" in service
    assert '""' in (REPO / "deploy/staging-diagnostics/hermes-staging-diagnostic.sudoers").read_text()
