from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gateway.lifecycle_lease import inspect_lifecycle_lease, reconcile_lifecycle_lease


def _run_command(home: Path, purpose: str, command: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "hermes_cli.main",
        "lifecycle-lease",
        "run",
        "--purpose",
        purpose,
        "--owner-token",
        f"{purpose}-crash-test",
        "--source-head",
        "a" * 40,
        "--source-tree",
        "b" * 40,
        "--artifact-sha256",
        "c" * 64,
        "--evidence-id",
        f"{purpose}-crash-evidence",
        "--expires-at",
        (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "--",
        *command,
    ]


def _wait_for_path(path: Path, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise AssertionError(f"controller exited early with {process.returncode}")
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def test_killed_controller_leaves_orphan_and_blocks_next_mutation(tmp_path):
    home = tmp_path / "profile-home"
    child_started = tmp_path / "child-started"
    forbidden_mutation = tmp_path / "forbidden-mutation"
    repo_root = str(__file__).split("/tests/", 1)[0]
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)

    # The child exits when its wrapper parent is killed, so this test leaves no
    # detached process behind. subprocess closes the lifecycle lock fd in the
    # child; only the wrapper owns the live flock.
    child_code = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(child_started)!r}).write_text('ready'); "
        "parent=os.getppid(); "
        "\nwhile os.getppid()==parent: time.sleep(0.02)"
    )
    wrapper = subprocess.Popen(
        _run_command(home, "lcm-activation", [sys.executable, "-c", child_code]),
        cwd=repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_path(child_started, wrapper)
        os.kill(wrapper.pid, signal.SIGKILL)
        assert wrapper.wait(timeout=5) == -signal.SIGKILL

        observed = inspect_lifecycle_lease(home=home)
        assert observed["status"] == "orphaned"
        assert observed["metadata"]["purpose"] == "lcm-activation"

        blocked = subprocess.run(
            _run_command(
                home,
                "soak",
                [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(forbidden_mutation)!r}).write_text('bad')",
                ],
            ),
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert blocked.returncode == 2
        assert "orphaned or stale ownership metadata" in blocked.stderr
        assert not forbidden_mutation.exists()

        reconciled = reconcile_lifecycle_lease(
            home=home,
            expected_metadata_sha256=observed["metadata_sha256"],
            expected_owner_token="lcm-activation-crash-test",
            expected_purpose="lcm-activation",
        )
        assert reconciled["status"] == "reconciled"
        assert inspect_lifecycle_lease(home=home)["status"] == "absent"
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=5)
