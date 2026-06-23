"""Tests for first-class external Kanban dispatcher mode.

The standalone daemon used to be an always-deprecated escape hatch that required
``--force`` even when the operator had explicitly disabled gateway-side dispatch
with ``kanban.dispatch_in_gateway=false``. That made the safe Hermes-VM pattern
awkward: keep Telegram in the gateway, but run worker spawning in a separate
service/VM.
"""
from __future__ import annotations

import argparse


def _daemon_args(**overrides):
    values = {
        "interval": 60.0,
        "max": None,
        "failure_limit": 2,
        "pidfile": None,
        "verbose": False,
        "force": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_daemon_runs_without_force_when_gateway_dispatch_disabled(monkeypatch):
    """kanban.dispatch_in_gateway=false is an explicit external-dispatch signal."""
    from hermes_cli import kanban as kb_cli

    calls = []
    monkeypatch.setattr(kb_cli.kb, "init_db", lambda: calls.append(("init_db",)))
    monkeypatch.setattr(
        kb_cli.kb,
        "run_daemon",
        lambda **kw: calls.append(("run_daemon", kw)),
    )
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"kanban": {"dispatch_in_gateway": False}},
    )

    rc = kb_cli._cmd_daemon(_daemon_args(interval=7.0, max=3, failure_limit=4))

    assert rc == 0
    assert calls[0] == ("init_db",)
    assert calls[1][0] == "run_daemon"
    assert calls[1][1]["interval"] == 7.0
    assert calls[1][1]["max_spawn"] == 3
    assert calls[1][1]["failure_limit"] == 4


def test_daemon_still_refuses_without_force_when_gateway_dispatch_may_be_active(monkeypatch, capsys):
    """Default config remains guarded so operators do not create two dispatchers."""
    from hermes_cli import kanban as kb_cli

    monkeypatch.setattr(kb_cli.kb, "init_db", lambda: (_ for _ in ()).throw(AssertionError("must not init")))
    monkeypatch.setattr(kb_cli.kb, "run_daemon", lambda **kw: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"kanban": {"dispatch_in_gateway": True}},
    )

    rc = kb_cli._cmd_daemon(_daemon_args(force=False))

    assert rc == 2
    err = capsys.readouterr().err
    assert "kanban.dispatch_in_gateway=false" in err
    assert "--force" in err


def test_daemon_force_still_runs_even_when_gateway_dispatch_may_be_active(monkeypatch):
    """The legacy escape hatch remains available for intentional double-dispatch tests."""
    from hermes_cli import kanban as kb_cli

    calls = []
    monkeypatch.setattr(kb_cli.kb, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(kb_cli.kb, "run_daemon", lambda **kw: calls.append("run"))
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"kanban": {"dispatch_in_gateway": True}},
    )

    rc = kb_cli._cmd_daemon(_daemon_args(force=True))

    assert rc == 0
    assert calls == ["init", "run"]
