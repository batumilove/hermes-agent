"""Updater owns pauses: refusal must precede all checkout obligations."""
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("failure", ["discovery", "left-running", "bare-notice", "record", "stop"])
def test_pause_refusal_prevents_commit_and_preserves_resume_debt(tmp_path, monkeypatch, failure):
    from hermes_cli import update_cmd_commit as commit
    from hermes_cli import update_cmd_posix_pause as pause
    from hermes_cli import update_pause_record as record
    from gateway import status

    entry = {"kind": "systemd", "scope": "user", "unit": "hermes-gateway-fixture", "pid": 424242,
             "home": str(tmp_path)}
    token = pause._empty_token()
    token.update(pause_id="adopted-fixture", resume_needed=True, retained_debt="do-not-drop")
    monkeypatch.setattr(pause, "_RUN", token)
    monkeypatch.setattr(pause, "_discover_systemd", lambda: [dict(entry)])
    monkeypatch.setattr(pause, "_discover_launchd", list)
    monkeypatch.setattr(pause, "_left_running", lambda u, j: (u, j, []))
    monkeypatch.setattr(pause, "_discover_bare", lambda _: ([], []))
    monkeypatch.setattr(status, "get_process_start_time", lambda _: 123.0)
    monkeypatch.setattr(record, "identity", lambda _: {"ct": "ct:123.0"})
    monkeypatch.setattr(record, "record_pause", lambda *a: token)
    monkeypatch.setattr(record, "mark_stop_requested", lambda *a, **k: None)
    monkeypatch.setattr(record, "sync", lambda *a: None)
    monkeypatch.setattr(pause, "_stop_gateways", lambda *a: None)
    # Recovery belongs to the existing exit/future-launch owner, not the refused commit point.
    monkeypatch.setattr(pause, "resume_paused_set", lambda *a: pytest.fail("inline resume discarded ownership"))
    monkeypatch.setattr(commit, "arm_commit_obligations", lambda *a: pytest.fail("mutable obligations armed"))
    monkeypatch.setattr(commit, "arm_tree_move", lambda *a, **k: pytest.fail("source mutation armed"))

    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure: " + failure)

    owner, name, replacement = {
        "discovery": (pause, "_discover_systemd", fail),
        "left-running": (pause, "_left_running", lambda u, j: ([], [], ["unit exclusion unproven"])),
        "bare-notice": (pause, "_discover_bare", lambda _: ([], ["supervised gateway remains"])),
        "record": (record, "record_pause", fail),
        "stop": (pause, "_stop_gateways", fail),
    }[failure]
    monkeypatch.setattr(owner, name, replacement)
    refused = commit.arm_commit_point(["git"], Path(tmp_path), "a" * 40)
    assert refused, "pause failure was accepted as permission to mutate source"
    assert pause.pause_at_commit_point() == refused
    assert token["retained_debt"] == "do-not-drop"
    assert token["resume_needed"] is True
    if failure == "stop":
        assert token["posix_units"][0]["pid"] == entry["pid"]
        assert token["posix_stopped"] is True


def test_successful_systemctl_stop_must_verify_terminal_unit(monkeypatch):
    from hermes_cli import update_cmd_posix_pause as pause
    from hermes_cli import update_cmd_fleet as fleet
    monkeypatch.setattr(pause, "_manage_cmd", lambda _: ["systemctl", "--user"])
    calls = []

    def systemctl(cmd, **kwargs):
        calls.append(cmd)
        if "stop" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="ActiveState=active\nSubState=running\nMainPID=525252\n", stderr="")

    monkeypatch.setattr(fleet, "_systemctl", systemctl)
    with pytest.raises(pause.PauseRefused):
        pause._stop_unit({"scope": "user", "unit": "hermes-gateway-fixture"})
    assert len(calls) > 1


def test_canceled_systemctl_stop_is_refused(monkeypatch):
    from hermes_cli import update_cmd_posix_pause as pause
    from hermes_cli import update_cmd_fleet as fleet
    monkeypatch.setattr(pause, "_manage_cmd", lambda _: ["systemctl", "--user"])
    monkeypatch.setattr(fleet, "_systemctl", lambda *a, **k: SimpleNamespace(returncode=1, stderr="Job canceled"))
    with pytest.raises(pause.PauseRefused, match="Job canceled"):
        pause._stop_unit({"scope": "user", "unit": "hermes-gateway-fixture"})


@pytest.mark.parametrize("outcome", ["timeout", "failed-listing", "unreadable-pid"])
def test_systemd_discovery_does_not_turn_unknown_into_empty(monkeypatch, outcome):
    from hermes_cli import gateway
    from hermes_cli import update_cmd_fleet as fleet
    from hermes_cli import update_cmd_posix_pause as pause
    import subprocess
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway, "_ensure_user_systemd_env", lambda: None)

    def listings(on_list_timeout=None):
        if outcome == "timeout":
            if on_list_timeout:
                on_list_timeout("user", subprocess.TimeoutExpired("fixture", 1))
            return
        yield "user", ["systemctl", "--user"], SimpleNamespace(
            returncode=1 if outcome == "failed-listing" else 0, stdout="hermes-gateway-fixture.service loaded active running fixture")

    monkeypatch.setattr(fleet, "_systemd_gateway_unit_listings", listings)
    monkeypatch.setattr(fleet, "_unit_main_pid", lambda *a: 0)
    with pytest.raises(pause.PauseRefused):
        pause._discover_systemd()


def test_partial_stop_failure_keeps_real_durable_resume_record(tmp_path, monkeypatch):
    from hermes_cli import update_cmd_posix_pause as pause
    from hermes_cli import update_pause_record as record
    import os
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    entry = {"kind": "systemd", "scope": "user", "unit": "hermes-gateway-fixture",
             "pid": os.getpid(), "home": str(tmp_path)}
    monkeypatch.setattr(pause, "_discover_systemd", lambda: [dict(entry)])
    monkeypatch.setattr(pause, "_discover_launchd", list)
    monkeypatch.setattr(pause, "_left_running", lambda u, j: (u, j, []))
    monkeypatch.setattr(pause, "_discover_bare", lambda _: ([], []))

    def failed_stop(token, entries):
        record.mark_stop_sent(token, os.getpid())
        raise pause.PauseRefused("Job canceled")

    monkeypatch.setattr(pause, "_stop_gateways", failed_stop)
    token = pause._empty_token()
    assert "Job canceled" in pause._pause(token)
    assert token["resume_needed"]
    # Inspect persisted bytes, not just the in-memory return value.
    import json
    records = [json.loads(path.read_text()) for path in tmp_path.rglob("*.json")]
    assert any(item.get("token", {}).get("resume_needed") and
               item["token"].get("posix_units", [{}])[0].get("pid") == os.getpid() for item in records)
