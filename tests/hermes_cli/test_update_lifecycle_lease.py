from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from hermes_cli import main as hermes_main


class _Lease:
    def __init__(self, events, release_error=None):
        self.events = events
        self.release_error = release_error

    def release(self):
        self.events.append("lease-release")
        if self.release_error is not None:
            raise self.release_error


def _args():
    return SimpleNamespace(
        plan=False,
        check=False,
        gateway=False,
        branch=None,
        yes=False,
        force=False,
        force_venv=False,
    )


def _isolate_update_boundary(monkeypatch, events):
    import hermes_cli.config as config
    import hermes_cli.update_lock as update_lock

    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(config, "detect_install_method", lambda _root: "git")
    monkeypatch.setattr(
        hermes_main,
        "_install_hangup_protection",
        lambda gateway_mode: events.append("io-install") or object(),
    )
    monkeypatch.setattr(
        hermes_main,
        "_finalize_update_output",
        lambda _state: events.append("io-finalize"),
    )

    class FakeUpdateLock:
        holder = None

        def acquire(self):
            events.append("update-lock-acquire")
            return True

        def release(self):
            events.append("update-lock-release")

    monkeypatch.setattr(update_lock, "UpdateLock", FakeUpdateLock)


def test_update_holds_one_deployment_lease_across_checkout_reconciliation(
    monkeypatch,
):
    events = []
    _isolate_update_boundary(monkeypatch, events)

    def acquire(**kwargs):
        events.append(("lease-acquire", kwargs))
        return _Lease(events)

    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction", acquire
    )
    monkeypatch.setattr(
        hermes_main,
        "_cmd_update_impl",
        # _cmd_update_impl contains checkout reconciliation (merge/reset and
        # rollback), so entering it only after acquisition proves that phase
        # is fenced without a nested self-deadlocking lease.
        lambda args, gateway_mode: events.append("checkout-reconciliation"),
    )

    hermes_main.cmd_update(_args())

    assert events[1] == "update-lock-acquire"
    assert events[2][0] == "lease-acquire"
    assert events[2][1]["purpose"] == "deployment"
    assert events[2][1]["operation"] == "hermes-update"
    assert events[3:6] == [
        "checkout-reconciliation",
        "lease-release",
        "update-lock-release",
    ]


def test_update_lease_block_prevents_impl_and_releases_update_lock(
    monkeypatch, capsys
):
    events = []
    _isolate_update_boundary(monkeypatch, events)

    def blocked(**kwargs):
        from hermes_cli.lifecycle_coordination import LifecycleCoordinationBlocked

        events.append("lease-blocked")
        raise LifecycleCoordinationBlocked("owned by soak")

    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction", blocked
    )
    monkeypatch.setattr(
        hermes_main,
        "_cmd_update_impl",
        lambda args, gateway_mode: events.append("impl"),
    )

    with pytest.raises(SystemExit) as exc_info:
        hermes_main.cmd_update(_args())

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "owned by soak" in captured.err
    assert "lifecycle-lease inspect" in captured.err
    assert "impl" not in events
    assert events[-2:] == ["update-lock-release", "io-finalize"]


def test_update_releases_deployment_lease_on_system_exit(monkeypatch):
    events = []
    _isolate_update_boundary(monkeypatch, events)
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: _Lease(events),
    )

    def exit_impl(args, gateway_mode):
        events.append("impl")
        sys.exit(7)

    monkeypatch.setattr(hermes_main, "_cmd_update_impl", exit_impl)

    with pytest.raises(SystemExit) as exc_info:
        hermes_main.cmd_update(_args())

    assert exc_info.value.code == 7
    assert events.index("lease-release") < events.index("update-lock-release")


def test_update_release_failure_is_not_hidden_by_primary_failure(monkeypatch):
    events = []
    _isolate_update_boundary(monkeypatch, events)
    release_error = RuntimeError("release refused metadata drift")
    monkeypatch.setattr(
        "hermes_cli.lifecycle_coordination.acquire_cli_lifecycle_transaction",
        lambda **kwargs: _Lease(events, release_error),
    )

    def fail_impl(args, gateway_mode):
        raise ValueError("primary update failure")

    monkeypatch.setattr(hermes_main, "_cmd_update_impl", fail_impl)

    with pytest.raises(RuntimeError, match="release refused metadata drift") as exc_info:
        hermes_main.cmd_update(_args())

    assert isinstance(exc_info.value.__cause__, ValueError)
