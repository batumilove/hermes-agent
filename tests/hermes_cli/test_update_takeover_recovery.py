"""Historical post-swap debt must not replay before PM preparation succeeds."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import update_pause_record as records

pytestmark = pytest.mark.platforms("linux")


@pytest.fixture
def prepared_checkout(tmp_path, monkeypatch):
    """Real Git, selected executable and PM currency records; no package installation."""
    from pm import client, paths
    from pm.environments import runtime_facts_path
    from pm.packages import Venv

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'takeover-recovery'\nversion = '1.0'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\nrevision = 1\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                  "commit", "-qm", "post-swap source")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    tools = tmp_path / "tools"
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tools))
    python = tools / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (tools / "facts.json").write_text(
        json.dumps({"packages": {"python": {"entry": "python"}}}), encoding="utf-8")
    # Exercise PM's real resident validator, not a substituted currency result.
    # This unit owns temp input/fact records; it does not execute a generation.
    monkeypatch.setattr(client, "is_runtime", lambda: True)
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    monkeypatch.setattr(records, "install_root", lambda: root)
    facts = runtime_facts_path(root)
    facts.parent.mkdir(parents=True)
    environment = facts.parent / "environments" / "prepared"
    environment.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_bytes((Path(sys.prefix) / "pyvenv.cfg").read_bytes())
    facts.write_text(json.dumps({"schema": 1, "packages": {"venv": {
        "stamp": Venv(root).expected_stamp([], plugin_dirs=[]), "extras": [],
        "environment": str(environment),
    }}}), encoding="utf-8")
    token = records.stamp_tree({
        "platform": "posix", "resume_needed": True, "profiles": {},
        "unmapped": [{"pid": 1000000000, "home": str(tmp_path / "home"),
                      "argv": ["/removed/historical/python", "-m", "hermes_cli.main", "gateway", "run"],
                      "takeover_args": ["gateway", "run"]}],
        "stop_requested": ["1000000000"], "stop_sent": ["1000000000"],
    }, root)
    return root, tools, facts, token


def _damage_fact(facts, key, value):
    body = json.loads(facts.read_text(encoding="utf-8"))
    body["packages"]["venv"][key] = value
    facts.write_text(json.dumps(body), encoding="utf-8")


def _swap_lock(root, token):
    # Historical takeover stamps its baseline after the source/lock swap.
    (root / "uv.lock").write_text("version = 1\nrevision = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qam", "new graph"],
                   check=True, capture_output=True)
    token["baselines"] = []
    records.stamp_tree(token, root)


@pytest.mark.parametrize("damage", [
    pytest.param(lambda r, t, f, debt: (t / "python" / "bin" / "python3").unlink(), id="missing-python"),
    pytest.param(lambda r, t, f, debt: (t / "facts.json").write_text("{", encoding="utf-8"), id="unknown-python"),
    pytest.param(lambda r, t, f, debt: f.unlink(), id="missing-facts"),
    pytest.param(lambda r, t, f, debt: f.write_text("{", encoding="utf-8"), id="unknown-facts"),
    pytest.param(lambda r, t, f, debt: _damage_fact(f, "stamp", 42), id="invalid-stamp"),
    pytest.param(lambda r, t, f, debt: _swap_lock(r, debt), id="stale-lock"),
    pytest.param(lambda r, t, f, debt: _damage_fact(f, "environment", str(r / "vanished-generation")),
                 id="missing-environment"),
])
def test_failed_preparation_preserves_historical_restart_debt(prepared_checkout, damage):
    from hermes_cli.update_cmd_windows import _resume_unless_torn

    root, tools, facts, token = prepared_checkout
    damage(root, tools, facts, token)
    original = copy.deepcopy(token)
    records.write(token, owner=records.UNOWNED)
    _resume_unless_torn(token)
    saved = records.read()["token"]
    assert saved["unmapped"] == original["unmapped"]
    assert saved["stop_sent"] == original["stop_sent"]
    assert saved["baselines"] == original["baselines"]
    assert token["resume_deferred"] and saved["resume_needed"]
    assert not saved.get("relaunch_retry"), "a preparation deferral spent a replay attempt"


def test_prepared_historical_debt_passes_real_pm_currency(prepared_checkout):
    import pm
    from hermes_cli._launchers import resolve_store_python, runtime_command

    root, tools, _facts, token = prepared_checkout
    assert pm.venv_is_current(project_root=root)
    python = tools / "python" / "bin" / "python3"
    assert resolve_store_python(root) == python
    assert runtime_command(root, token["unmapped"][0]["takeover_args"])[0] == str(python)
    assert records.tree_is_whole(token, root) == (True, "")


@pytest.mark.parametrize("args", [None, [], False, 0, "gateway run", [None]])
def test_invalid_historical_args_cannot_replay_through_caller_python(prepared_checkout, args):
    from hermes_cli.update_cmd_windows import _resume_unless_torn

    root, _tools, _facts, token = prepared_checkout
    token["unmapped"][0]["takeover_args"] = args
    records.write(token, owner=records.UNOWNED)
    saved = records.read()["token"]
    whole, why = records.tree_is_whole(saved, root)
    assert not whole and "arguments" in why
    assert records.read()["token"] == saved
    _resume_unless_torn(token)
    deferred = records.read()["token"]
    assert deferred["unmapped"] == saved["unmapped"]
    assert deferred["stop_sent"] == saved["stop_sent"]
    assert deferred["baselines"] == saved["baselines"]
    assert token["resume_deferred"] and deferred["resume_needed"]
    assert not deferred.get("relaunch_retry")


def test_historical_requirement_survives_merge_until_its_debt_is_retired(prepared_checkout):
    root, tools, _facts, historical = prepared_checkout
    records.write(historical, owner=records.UNOWNED)
    saved = records.read()["token"]
    modern = records.stamp_tree({"platform": "posix", "resume_needed": True,
                               "profiles": {"other": 1000000001}, "unmapped": []}, root)
    merged = records.merge_into(modern, saved)
    records.sync(merged)
    merged = records.read()["token"]
    (tools / "python" / "bin" / "python3").unlink()
    assert not records.tree_is_whole(merged, root)[0]
    merged["unmapped"] = []  # verified replay retires this entry, not its unrelated peer
    records.sync(merged)
    assert records.read()["token"]["profiles"] == {"other": 1000000001}
    assert records.tree_is_whole(merged, root) == (True, "")
