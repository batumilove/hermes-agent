"""Shutdown watchdog + loop heartbeat coverage for #66892.

The drain path is asyncio-based; a frozen loop makes every asyncio timeout
structurally unable to fire. These tests pin the out-of-loop backstop
(thread watchdog) and the loop-liveness heartbeat file contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway.shutdown_watchdog import (
    DEFAULT_SHUTDOWN_WATCHDOG_GRACE_S,
    arm_shutdown_watchdog,
    get_loop_heartbeat_path,
    get_shutdown_watchdog_dump_path,
    loop_heartbeat_forever,
    resolve_shutdown_watchdog_delay,
    write_loop_heartbeat,
)

def test_resolve_shutdown_watchdog_delay_adds_grace():
    assert resolve_shutdown_watchdog_delay(180) == 180 + DEFAULT_SHUTDOWN_WATCHDOG_GRACE_S
    assert resolve_shutdown_watchdog_delay(0) == DEFAULT_SHUTDOWN_WATCHDOG_GRACE_S
    assert resolve_shutdown_watchdog_delay("bad") == DEFAULT_SHUTDOWN_WATCHDOG_GRACE_S
    assert resolve_shutdown_watchdog_delay(10, grace_s=5) == 15.0


def test_arm_shutdown_watchdog_fires_with_dump_and_exit(tmp_path):
    done = threading.Event()
    fired = threading.Event()
    dump = tmp_path / "logs" / "watchdog.log"
    snapshot_calls = []
    exit_codes = []

    def snapshot():
        snapshot_calls.append(1)
        return {"active_agents": 1, "draining": True}

    def fake_exit(code):
        exit_codes.append(code)
        fired.set()

    with patch("gateway.shutdown_watchdog.os._exit", side_effect=fake_exit):
        arm_shutdown_watchdog(
            0.15,
            done_event=done,
            snapshot_fn=snapshot,
            dump_path=dump,
            exit_code=9,
        )
        assert fired.wait(timeout=5.0), "watchdog did not fire"

    assert exit_codes == [9]
    assert snapshot_calls == [1]
    assert dump.is_file()
    text = dump.read_text(encoding="utf-8")
    assert "shutdown_watchdog_fired" in text
    assert "faulthandler dump" in text
    assert get_shutdown_watchdog_dump_path(tmp_path).name == "gateway-shutdown-watchdog.log"


def _run_watchdog_child(code: str, *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_shutdown_watchdog_hard_exits_when_snapshot_blocks():
    started = time.monotonic()
    completed = _run_watchdog_child(
        """
        import threading
        from gateway.shutdown_watchdog import arm_shutdown_watchdog

        def blocked_snapshot():
            threading.Event().wait()

        arm_shutdown_watchdog(0.2, snapshot_fn=blocked_snapshot, exit_code=42)
        print("WATCHDOG_ARMED", flush=True)
        threading.Event().wait()
        """,
    )
    assert "WATCHDOG_ARMED" in completed.stdout, completed.stderr
    assert completed.returncode != 0
    assert time.monotonic() - started < 3.0


def test_shutdown_watchdog_hard_exits_when_dump_open_blocks(tmp_path):
    fifo = tmp_path / "blocked-watchdog-dump"
    os.mkfifo(fifo)
    started = time.monotonic()
    completed = _run_watchdog_child(
        f"""
        import threading
        from pathlib import Path
        from gateway.shutdown_watchdog import arm_shutdown_watchdog

        arm_shutdown_watchdog(
            0.2,
            snapshot_fn=lambda: {{}},
            dump_path=Path({str(fifo)!r}),
            exit_code=43,
        )
        print("WATCHDOG_ARMED", flush=True)
        threading.Event().wait()
        """,
    )
    assert "WATCHDOG_ARMED" in completed.stdout, completed.stderr
    assert completed.returncode != 0
    assert time.monotonic() - started < 3.0


@pytest.mark.skipif(not hasattr(os, "set_blocking"), reason="requires blocking pipe control")
def test_shutdown_watchdog_hard_exit_does_not_block_on_full_stderr_pipe():
    """The final backstop must not dump to a potentially blocked stderr FD."""
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    while True:
        try:
            os.write(write_fd, b"x" * 65536)
        except BlockingIOError:
            break
    os.set_blocking(write_fd, True)

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    code = textwrap.dedent(
        """
        import threading
        from gateway.shutdown_watchdog import arm_shutdown_watchdog

        def blocked_snapshot():
            threading.Event().wait()

        arm_shutdown_watchdog(0.2, snapshot_fn=blocked_snapshot, exit_code=45)
        print("WATCHDOG_ARMED", flush=True)
        threading.Event().wait()
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=write_fd,
        text=True,
    )
    os.close(write_fd)
    try:
        try:
            stdout, _ = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            pytest.fail(
                "hard-exit fallback blocked while writing diagnostics to a full stderr pipe; "
                f"stdout={stdout!r}"
            )
        assert "WATCHDOG_ARMED" in stdout
        assert proc.returncode != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1.0)
        os.close(read_fd)


def test_shutdown_watchdog_hard_exit_is_disarmed_on_completion():
    completed = _run_watchdog_child(
        """
        import threading
        import time
        from gateway.shutdown_watchdog import arm_shutdown_watchdog

        done = threading.Event()
        arm_shutdown_watchdog(0.2, done_event=done, exit_code=44)
        done.set()
        time.sleep(1.5)
        """,
    )
    assert completed.returncode == 0, completed.stderr


