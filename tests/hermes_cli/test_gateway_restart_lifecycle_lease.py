from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import hermes_cli.gateway as gateway_cli


class _Lease:
    def __init__(self, events, release_error=None):
        self.events = events
        self.release_error = release_error

    def release(self):
        self.events.append("lease-release")
        if self.release_error is not None:
            raise self.release_error


def _args(**overrides):
    values = {
        "gateway_command": "restart",
        "system": False,
        "all": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _isolate_restart(monkeypatch, events):
    monkeypatch.setattr(
        "tools.process_registry._is_supervised_gateway_process", lambda: False
    )
    monkeypatch.setattr(
        gateway_cli, "_dispatch_all_via_service_manager_if_s6", lambda action: False
    )
    monkeypatch.setattr(
        gateway_cli, "_dispatch_via_service_manager_if_s6", lambda action: False
    )
    monkeypatch.setattr(gateway_cli, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(
        gateway_cli,
        "get_systemd_unit_path",
        lambda system=False: SimpleNamespace(exists=lambda: True),
    )
    monkeypatch.setattr(gateway_cli, "is_macos", lambda: False)
    monkeypatch.setattr(gateway_cli, "is_windows", lambda: False)
    monkeypatch.setattr(gateway_cli, "get_systemd_linger_status", lambda: (True, "ok"))


def test_gateway_restart_holds_common_lease_before_systemd_mutation(
    monkeypatch,
):
    events = []
    _isolate_restart(monkeypatch, events)

    def acquire(**kwargs):
        events.append(("lease-acquire", kwargs))
        return _Lease(events)

    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction", acquire
    )
    monkeypatch.setattr(
        gateway_cli,
        "systemd_restart",
        lambda system=False: events.append("systemd-restart"),
    )

    gateway_cli._gateway_command_inner(_args())

    assert events[0][0] == "lease-acquire"
    assert events[0][1]["purpose"] == "gateway-restart"
    assert events[0][1]["operation"] == "hermes-gateway-restart"
    assert events[1:] == ["systemd-restart", "lease-release"]


def test_gateway_restart_common_lease_block_prevents_backend_mutation(
    monkeypatch, capsys
):
    events = []
    _isolate_restart(monkeypatch, events)

    def blocked(**kwargs):
        from hermes_cli.lifecycle_coordination import LifecycleCoordinationBlocked

        raise LifecycleCoordinationBlocked("owned by deployment")

    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction", blocked
    )
    monkeypatch.setattr(
        gateway_cli,
        "systemd_restart",
        lambda system=False: events.append("systemd-restart"),
    )

    with pytest.raises(SystemExit) as exc_info:
        gateway_cli._gateway_command_inner(_args())

    assert exc_info.value.code == 2
    assert "systemd-restart" not in events
    captured = capsys.readouterr()
    assert "owned by deployment" in captured.out
    assert "lifecycle-lease inspect" in captured.out


def test_gateway_restart_self_guard_runs_before_common_lease(monkeypatch):
    events = []
    monkeypatch.setattr(
        "tools.process_registry._is_supervised_gateway_process", lambda: True
    )
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: events.append("lease-acquire"),
    )

    with pytest.raises(SystemExit) as exc_info:
        gateway_cli._gateway_command_inner(_args())

    assert exc_info.value.code == 1
    assert events == []


def test_gateway_restart_releases_common_lease_on_service_failure(
    monkeypatch,
):
    events = []
    _isolate_restart(monkeypatch, events)
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: _Lease(events),
    )

    def fail_restart(system=False):
        events.append("systemd-restart")
        raise subprocess.CalledProcessError(1, ["systemctl", "restart"])

    monkeypatch.setattr(gateway_cli, "systemd_restart", fail_restart)

    with pytest.raises(SystemExit) as exc_info:
        gateway_cli._gateway_command_inner(_args())

    assert exc_info.value.code == 1
    assert events == ["systemd-restart", "lease-release"]


def test_gateway_restart_releases_common_lease_on_s6_early_return(monkeypatch):
    events = []
    _isolate_restart(monkeypatch, events)
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: _Lease(events),
    )
    monkeypatch.setattr(
        gateway_cli,
        "_dispatch_via_service_manager_if_s6",
        lambda action: events.append(("s6", action)) or True,
    )
    monkeypatch.setattr(
        gateway_cli,
        "systemd_restart",
        lambda system=False: events.append("systemd-restart"),
    )

    gateway_cli._gateway_command_inner(_args())

    assert events == [("s6", "restart"), "lease-release"]


def test_gateway_restart_release_failure_chains_primary_failure(monkeypatch):
    events = []
    _isolate_restart(monkeypatch, events)
    release_error = RuntimeError("restart lease release refused")
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: _Lease(events, release_error),
    )

    def fail_restart(system=False):
        raise KeyboardInterrupt("primary")

    monkeypatch.setattr(gateway_cli, "systemd_restart", fail_restart)

    with pytest.raises(RuntimeError, match="restart lease release refused") as exc_info:
        gateway_cli._gateway_command_inner(_args())

    assert isinstance(exc_info.value.__cause__, KeyboardInterrupt)


def test_gateway_restart_preserves_existing_behavior_when_posix_leases_are_unavailable(
    monkeypatch, capsys
):
    events = []
    _isolate_restart(monkeypatch, events)
    monkeypatch.setattr("gateway.lifecycle_lease.fcntl", None)
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: pytest.fail("non-POSIX restart must not acquire POSIX lease"),
    )
    monkeypatch.setattr(
        gateway_cli,
        "systemd_restart",
        lambda system=False: events.append("systemd-restart"),
    )

    gateway_cli._gateway_command_inner(_args())

    assert events == ["systemd-restart"]
    assert "lifecycle coordination is unavailable" in capsys.readouterr().out.lower()
