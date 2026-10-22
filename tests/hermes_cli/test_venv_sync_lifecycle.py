"""Launch-time deployment completion shares the real profile lifecycle lease.

The completion seam records generation promotion and pending-tail discharge;
Git identity, checkout locking, lifecycle ownership and reconciliation are real.
This does not claim fresh-install or dependency-build acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from gateway.lifecycle_lease import inspect_lifecycle_lease, reconcile_lifecycle_lease
from hermes_cli import venv_sync
from hermes_cli.lifecycle_coordination import acquire_cli_lifecycle_transaction, lifecycle_coordination_available
from pm.environments import install_state_dir, selected_venv


@pytest.fixture
def launch(tmp_path, monkeypatch):
    import pm
    from hermes_cli import _launchers

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='completion-fixture'\n", encoding="utf-8")
    (root / "install-stamp.json").write_text('{"updateMechanism":"self"}', encoding="utf-8")
    for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"),
                 ("config", "user.name", "test"), ("add", "-A"), ("commit", "-qm", "source")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=30)
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_DISABLE_LAZY_INSTALLS", raising=False)
    monkeypatch.setattr(pm, "venv_is_current", lambda **kwargs: False)
    monkeypatch.setattr(_launchers, "resolve_store_python", lambda *args, **kwargs: Path(sys.executable))
    monkeypatch.setattr(venv_sync, "publish_launchers", lambda *args, **kwargs: None)
    state = install_state_dir(root)
    selected = state / "environments" / "selected" / "venv"
    promoted = state / "environments" / "promoted" / "venv"
    for environment in (selected, promoted):
        environment.mkdir(parents=True)
        (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
    facts = state / "facts.json"
    facts.write_text(json.dumps({"packages": {"venv": {"environment": str(selected)}}}), encoding="utf-8")
    pending = venv_sync.arm_completion(root)
    calls = []

    def finish(source, **kwargs):
        observed = inspect_lifecycle_lease(home=home) if lifecycle_coordination_available() else None
        calls.append(observed)
        facts.write_text(json.dumps({"packages": {"venv": {"environment": str(promoted)}}}), encoding="utf-8")
        pending.unlink()

    monkeypatch.setattr(venv_sync, "_finish_source_update", finish)
    return root, home, facts, pending, selected, promoted, calls


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("state", ["active", "orphaned", "tampered"])
def test_launch_refuses_ambiguous_lifecycle_before_promoting_generation(launch, state):
    root, home, facts, pending, selected, _promoted, calls = launch
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="gateway-restart", operation="other-controller",
    )
    if state != "active":
        lease._handle.close()
    metadata = home / ".lifecycle_transaction_lease.json"
    if state == "tampered":
        metadata.write_text("invalid ownership", encoding="utf-8")
    records = {path: path.read_bytes() for path in (metadata, facts, pending)}
    try:
        with pytest.raises(SystemExit) as blocked:
            venv_sync.prepare_launch(root, [])
        assert blocked.value.code == 2
        assert calls == []
        assert selected_venv(root) == selected
        assert {path: path.read_bytes() for path in records} == records
        assert not (home / ".hermes-update-in-progress").exists()
    finally:
        if state == "active":
            lease.release()


@pytest.mark.platforms("posix")
def test_exact_orphan_reconciliation_allows_one_owned_completion(launch):
    root, home, _facts, pending, _selected, promoted, calls = launch
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="deployment", operation="interrupted-update",
    )
    lease._handle.close()
    observed = inspect_lifecycle_lease(home=home)
    reconcile_lifecycle_lease(
        home=home, expected_metadata_sha256=observed["metadata_sha256"],
        expected_owner_token=observed["metadata"]["owner_token"],
        expected_purpose=observed["metadata"]["purpose"],
    )
    assert venv_sync.prepare_launch(root, []) == Path(sys.executable)
    assert len(calls) == 1 and calls[0]["status"] == "active"
    assert calls[0]["metadata"]["purpose"] == "deployment"
    assert calls[0]["metadata"]["provenance"]["source_head"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8", timeout=30,
    ).strip()
    assert calls[0]["metadata"]["provenance"]["source_tree"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True, encoding="utf-8", timeout=30,
    ).strip()
    assert selected_venv(root) == promoted
    assert not pending.exists()
    assert inspect_lifecycle_lease(home=home)["status"] == "absent"


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("failure", [RuntimeError("tail failed"), KeyboardInterrupt()])
def test_failed_or_cancelled_tail_releases_ownership_and_stays_pending(launch, monkeypatch, failure):
    root, home, facts, pending, selected, _promoted, _calls = launch
    records = {path: path.read_bytes() for path in (facts, pending)}

    def fail(*args, **kwargs):
        assert inspect_lifecycle_lease(home=home)["status"] == "active"
        raise failure

    monkeypatch.setattr(venv_sync, "_finish_source_update", fail)
    with pytest.raises(type(failure)):
        venv_sync.prepare_launch(root, [])
    assert selected_venv(root) == selected
    assert {path: path.read_bytes() for path in records} == records
    assert inspect_lifecycle_lease(home=home)["status"] == "absent"
    assert not (home / ".hermes-update-in-progress").exists()


@pytest.mark.platforms("posix")
def test_current_launch_without_a_tail_does_not_reacquire_other_controller_lease(launch, monkeypatch):
    import pm

    root, home, _facts, pending, selected, _promoted, calls = launch
    pending.unlink()
    monkeypatch.setattr(pm, "venv_is_current", lambda **kwargs: True)
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="gateway-restart", operation="other-controller",
    )
    observed = inspect_lifecycle_lease(home=home)
    try:
        assert venv_sync.prepare_launch(root, []) is None
        assert calls == [] and selected_venv(root) == selected
        assert inspect_lifecycle_lease(home=home) == observed
    finally:
        lease.release()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("current", [True, False])
def test_actual_updater_child_keeps_parent_ownership_without_running_tail(launch, current):
    import os
    from hermes_cli.update_lock import UpdateLock

    root, home, facts, pending, selected, promoted, calls = launch
    lock = UpdateLock(install_root=root)
    assert lock.acquire()
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="deployment", operation="parent-update",
    )
    observed = inspect_lifecycle_lease(home=home)
    code = (
        "import json, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "import pm\nfrom hermes_cli import _launchers, venv_sync\n"
        "from gateway.lifecycle_lease import inspect_lifecycle_lease\n"
        f"home, root = Path({str(home)!r}), Path({str(root)!r})\n"
        f"observed = {observed!r}\ncurrent = {{'value': {current!r}}}\n"
        "pm.venv_is_current = lambda **kw: current['value']\n"
        "def sync(*args, **kwargs):\n"
        "    assert inspect_lifecycle_lease(home=home) == observed\n"
        f"    Path({str(facts)!r}).write_text({json.dumps({'packages': {'venv': {'environment': str(promoted)}}})!r}, encoding='utf-8')\n"
        "    current['value'] = True\n"
        "def forbidden(*args, **kwargs):\n    raise AssertionError('updater child ran tail')\n"
        "venv_sync._sync_source_dependencies = sync\nvenv_sync._finish_source_update = forbidden\n"
        "venv_sync.publish_launchers = lambda *a, **kw: None\n"
        "_launchers.resolve_store_python = lambda *a, **kw: Path(sys.executable)\n"
        "venv_sync.prepare_launch(root, [])\n"
        "assert inspect_lifecycle_lease(home=home) == observed\n"
        "print('parent ownership retained')\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code], cwd=root, env=dict(os.environ),
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "parent ownership retained" in result.stdout
        assert calls == [] and pending.exists()
        assert selected_venv(root) == (selected if current else promoted)
        assert inspect_lifecycle_lease(home=home) == observed
    finally:
        lease.release()
        lock.release()


@pytest.mark.platforms("windows")
def test_native_non_posix_launch_retains_existing_completion_fallback(launch, capsys):
    root, _home, _facts, pending, _selected, promoted, calls = launch
    assert venv_sync.prepare_launch(root, []) == Path(sys.executable)
    assert len(calls) == 1 and selected_venv(root) == promoted
    assert not pending.exists()
    assert "lifecycle coordination is unavailable on this platform" in capsys.readouterr().err


@pytest.mark.platforms("posix")
def test_ordinary_operator_cli_reaches_exact_reconciliation_without_completing_tail(launch):
    """Execute the real CLI after the launch-preparation seam, without a fresh installer."""
    import os

    root, home, facts, pending, selected, _promoted, calls = launch
    lease = acquire_cli_lifecycle_transaction(
        home=home, repo_root=root, purpose="deployment", operation="interrupted-update",
    )
    lease._handle.close()
    observed = inspect_lifecycle_lease(home=home)
    metadata = home / ".lifecycle_transaction_lease.json"
    records = {path: path.read_bytes() for path in (metadata, facts, pending)}
    code = (
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "fixture_root = Path(sys.argv.pop(1))\n"
        "import pm\npm.venv_is_current = lambda **kw: False\n"
        "from hermes_cli.venv_sync import prepare_launch\n"
        "assert prepare_launch(fixture_root, sys.argv[1:]) is None\n"
        "from hermes_cli.main import main\nmain()\n"
    )

    def operator(*args):
        result = subprocess.run(
            [sys.executable, "-c", code, str(root), "-p", "default", "lifecycle-lease", *args, "--json"],
            cwd=root, env=dict(os.environ), capture_output=True, text=True, encoding="utf-8",
            timeout=60, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    assert operator("inspect") == observed
    assert {path: path.read_bytes() for path in records} == records
    # This disruptive controller command is not part of the safe rescue exemption.
    with pytest.raises(SystemExit) as blocked:
        venv_sync.prepare_launch(root, ["lifecycle-lease", "run"])
    assert blocked.value.code == 2 and calls == []
    assert {path: path.read_bytes() for path in records} == records
    reconciled = operator(
        "reconcile", "--expected-metadata-sha256", observed["metadata_sha256"],
        "--expected-owner-token", observed["metadata"]["owner_token"],
        "--expected-purpose", observed["metadata"]["purpose"],
    )
    assert reconciled["status"] == "reconciled"
    assert reconciled["metadata_sha256"] == observed["metadata_sha256"]
    assert inspect_lifecycle_lease(home=home)["status"] == "absent"
    assert not metadata.exists()
    assert {path: path.read_bytes() for path in (facts, pending)} == {
        path: records[path] for path in (facts, pending)
    }
    assert selected_venv(root) == selected and calls == []
