"""Real operator/namespace boundaries for interrupted upgrade fixture recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

import pytest

from gateway.lifecycle_lease import inspect_lifecycle_lease, reconcile_lifecycle_lease
from hermes_cli.lifecycle_coordination import acquire_cli_lifecycle_transaction
from tests.e2e.core.dashboard import _reaper
from tests.e2e.core.upgrade import _helpers as H
from tests.e2e.core.upgrade import _lifecycle_fixture as L
from tests.e2e.core.upgrade.handoff._nshost import NamespaceHost

pytestmark = pytest.mark.platforms("linux")


@dataclass
class _Endpoint:
    """Real source CLI on the canonical PM test interpreter, under one isolated profile.

    This tests the operator helper without claiming installer/generation acceptance.
    The full PM and Git E2Es separately use their actual installed managed interpreter.
    """
    root: Path
    env: dict[str, str]
    python: str = sys.executable
    checkout: Path = H.WORKTREE

    @property
    def hermes_home(self) -> Path:
        return Path(self.env["HERMES_HOME"])

    def run(self, argv, *, cwd=None, timeout=60):
        return H.run(argv, env=self.env, cwd=cwd or self.checkout,
                     writable=[self.root], timeout=timeout)


def _retry_transaction(sb: _Endpoint, forbidden: Path) -> subprocess.CompletedProcess:
    code = (
        "from pathlib import Path\n"
        "from hermes_cli.lifecycle_coordination import cli_update_transaction\n"
        f"with cli_update_transaction(Path({str(sb.hermes_home)!r}), Path({str(sb.checkout)!r})):\n"
        f"    Path({str(forbidden)!r}).write_text('must not mutate')\n"
    )
    return sb.run([sb.python, "-c", code])


@pytest.mark.parametrize("without_bwrap", [False, True], ids=["detected-sandbox", "without-bwrap"])
def test_fixture_recovers_only_its_real_namespace_controller_with_sticky_profile(tmp_path, monkeypatch, without_bwrap):
    if without_bwrap:
        # Exercise the real Linux fallback used by CI when optional bwrap is unavailable.
        monkeypatch.setattr(H, "BWRAP_OK", False)
    sb = _Endpoint(tmp_path, H.isolated_env(tmp_path, pythonpath=H.WORKTREE))
    for args in [("create", "fixture-sticky", "--no-alias"), ("use", "fixture-sticky")]:
        result = sb.run([sb.python, "-m", "hermes_cli.main", "profile", *args])
        assert result.returncode == 0, result.stdout + result.stderr
    ready = tmp_path / "controller-ready"
    forbidden = tmp_path / "forbidden-mutation"
    code = (
        "from pathlib import Path\n"
        "from threading import Event\n"
        "from hermes_cli.lifecycle_coordination import acquire_cli_lifecycle_transaction\n"
        f"lease = acquire_cli_lifecycle_transaction(home=Path({str(sb.hermes_home)!r}), "
        f"repo_root=Path({str(sb.checkout)!r}), purpose='deployment', operation='fixture-controller')\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "Event().wait(300)\n"
    )
    host = NamespaceHost(tmp_path, sb.env)
    try:
        host.spawn([sb.python, "-c", code], log=tmp_path / "controller.log")
        H.wait_for(ready.exists, timeout=20, interval=0.05, what="real controller lease")
        direct = inspect_lifecycle_lease(home=sb.hermes_home)
        assert direct["status"] == "active", direct
        assert L.inspect(sb) == direct
        observed = L.remember_update_lease(sb, host)
        assert observed["metadata"]["pid"] in {p["pid"] for p in host.procs()}
        if without_bwrap:
            # The fallback exposes the actual host PID, so require collection
            # of this exact process as well as an empty live-process census.
            pid = observed["metadata"]["pid"]
            before = _reaper.stat(pid)
            assert before is not None
        assert L.close_namespace(sb, host, lambda: _retry_transaction(sb, forbidden)) == []
        if without_bwrap:
            after = _reaper.stat(pid)
            assert after is None or after[19] != before[19], "owned controller remains a zombie"
        assert inspect_lifecycle_lease(home=sb.hermes_home)["status"] == "absent"
        assert not forbidden.exists()
    finally:
        host.close()


def test_fixture_closes_namespace_but_does_not_reconcile_unremembered_orphan(tmp_path):
    sb = _Endpoint(tmp_path, H.isolated_env(tmp_path, pythonpath=H.WORKTREE))
    lease = acquire_cli_lifecycle_transaction(
        home=sb.hermes_home, repo_root=sb.checkout,
        purpose="deployment", operation="foreign-controller",
    )
    lease._handle.close()
    observed = inspect_lifecycle_lease(home=sb.hermes_home)
    host = NamespaceHost(tmp_path, sb.env)
    try:
        with pytest.raises(AssertionError, match="unremembered orphan"):
            L.close_namespace(sb, host, lambda: _retry_transaction(sb, tmp_path / "forbidden"))
        assert inspect_lifecycle_lease(home=sb.hermes_home) == observed
    finally:
        host.close()
        reconcile_lifecycle_lease(
            home=sb.hermes_home,
            expected_metadata_sha256=observed["metadata_sha256"],
            expected_owner_token=observed["metadata"]["owner_token"],
            expected_purpose=observed["metadata"]["purpose"],
        )
