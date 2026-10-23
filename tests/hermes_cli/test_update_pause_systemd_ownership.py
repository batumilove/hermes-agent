"""An active unknown unit is not proof that source mutation is safe."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def active_unit(tmp_path, monkeypatch):
    from hermes_cli import dashboard_procs, gateway
    from hermes_cli import update_cmd_fleet as fleet
    from hermes_cli import update_cmd_posix_pause as pause
    from hermes_cli import update_fleet_scope as scope

    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway, "_ensure_user_systemd_env", lambda: None)
    monkeypatch.setattr(fleet, "_systemd_gateway_unit_listings", lambda **kw: iter([
        ("user", ["systemctl", "--user"], SimpleNamespace(returncode=0,
         stdout="hermes-gateway-fixture.service loaded active running fixture"))]))
    monkeypatch.setattr(fleet, "_unit_main_pid", lambda *a: 424242)
    monkeypatch.setattr(scope, "update_scope_homes", lambda: {tmp_path, tmp_path / "profiles" / "worker"})

    def unreadable_environment(pid):
        raise PermissionError("fixture process environment denied")

    monkeypatch.setattr(dashboard_procs, "_hermes_home_for_pid", unreadable_environment)
    monkeypatch.setattr(pause, "_unit_cgroup", lambda *a: "/fixture.service")
    monkeypatch.setattr(pause, "_home_of", lambda pid: str(tmp_path))
    return fleet, pause


@pytest.mark.parametrize("environment", [None, "", "HERMES_HOME=/foreign-install"])
def test_commit_gate_distinguishes_unknown_from_proven_foreign(active_unit, tmp_path, monkeypatch, environment):
    from hermes_cli import update_cmd_commit as commit
    from hermes_cli import update_pause_record as record

    fleet, pause = active_unit
    calls = []

    def systemctl(cmd, **kwargs):
        assert "--property=Environment" in cmd
        calls.append("ownership-read")
        return SimpleNamespace(returncode=1 if environment is None else 0, stdout=environment or "")

    monkeypatch.setattr(fleet, "_systemctl", systemctl)
    monkeypatch.setattr(pause, "_discover_launchd", list)
    monkeypatch.setattr(pause, "_left_running", lambda u, j: (u, j, []))
    # The real bare discovery excludes service PIDs; there is no fallback ownership proof.
    monkeypatch.setattr(pause, "_discover_bare", lambda _: ([], []))
    monkeypatch.setattr(pause, "_stop_gateways", lambda *a: pytest.fail("unit must not be stopped"))
    monkeypatch.setattr(record, "record_pause", lambda *a: pytest.fail("pause debt must not change"))
    monkeypatch.setattr(pause, "resume_paused_set", lambda *a: pytest.fail("recovery owner must not change"))
    token = pause._empty_token()
    token.update(resume_needed=True, pause_id="retained-debt")
    monkeypatch.setattr(pause, "_RUN", token)
    monkeypatch.setattr(commit, "arm_commit_obligations", lambda *a: calls.append("obligations"))
    monkeypatch.setattr(commit, "arm_tree_move", lambda *a, **kw: calls.append("tree-move"))

    refused = commit.arm_commit_point(["git"], tmp_path, "a" * 40)
    assert calls[0] == "ownership-read"
    assert token["resume_needed"] is True
    assert token["pause_id"] == "retained-debt"
    if environment == "HERMES_HOME=/foreign-install":
        assert refused is None
        assert calls == ["ownership-read", "obligations", "tree-move"]
    else:
        assert refused and "ownership" in refused
        assert calls == ["ownership-read"]


@pytest.mark.parametrize("profile", ["", "profiles/worker"])
def test_owned_install_and_fleet_profile_remain_discovered(active_unit, tmp_path, monkeypatch, profile):
    fleet, pause = active_unit
    home = tmp_path / profile
    monkeypatch.setattr(fleet, "_systemctl", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=f"HERMES_HOME={home}"))
    units = pause._discover_systemd()
    assert len(units) == 1
    assert units[0]["pid"] == 424242
    assert units[0]["unit"] == "hermes-gateway-fixture"
