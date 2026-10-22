"""Native Windows finish custody behavior, isolated within the existing per-file deadline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
import subprocess
import sys
import time
import pytest
from tests.scripts.desktop_update.windows_commit_outcome_support import (
    _CHECKOUT_HOLDER_UNTIL_RELEASE,
)
from tests.scripts.desktop_update.windows_commit_outcome_support import _handoff
from tests.scripts.desktop_update.windows_handoff_support import _log
from tests.waiting import wait_until


@pytest.mark.platforms("windows")
def test_result_finished_at_is_stamped_after_the_r6_release_wait(
    tmp_path: Path,
) -> None:
    # The relaunched Desktop drops a non-manual result whose finished_at is 30 minutes old; the
    # R6 wait lasts up to 2 h. The result must carry the time the hand-off actually finished.
    holders = []
    release = tmp_path / "release-checkout-holder"
    home = tmp_path / "home"

    def hold_checkout(_home: Path, install: Path) -> None:
        holder = subprocess.Popen(
            [sys.executable, "-c", _CHECKOUT_HOLDER_UNTIL_RELEASE,
             str(install / ".hermes-update.lock"), str(release)]
        )
        holders.append(holder)
        ready = Path(str(release) + ".ready")
        wait_until(lambda: ready.exists() or holder.poll() is not None,
                   timeout=30, what="checkout holder acquired its native lock")
        assert ready.exists() and holder.poll() is None, "checkout holder exited before hand-off"

    with ThreadPoolExecutor(max_workers=1) as worker:
        handoff = worker.submit(
            copy_context().run, _handoff, tmp_path, "-NoGateway", prepare=hold_checkout
        )
        try:
            wait_until(lambda: "keeping the update marker" in _log(home) or handoff.done(),
                       timeout=150, what="hand-off reached its native R6 release wait")
            assert not handoff.done(), handoff.result()
            assert holders and holders[0].poll() is None
            # Cross timestamp seconds only after observing R6, so an early stamp cannot pass.
            release_second = int(time.time()) + 2
            wait_until(lambda: time.time() >= release_second, timeout=5,
                       what="later checkout release timestamp")
            released_at = int(time.time())
            release.touch()
            code, out, _, result, home = handoff.result(timeout=150)
        finally:
            release.touch()
            for proc in holders:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=30)
    assert code == 0, out
    log = (home / "logs/desktop-update-handoff.log").read_text(encoding="utf-8-sig")
    assert "keeping the update marker" in log, log
    assert result["ok"] is True and result["finished_at"] >= released_at, (
        result
    )


@pytest.mark.platforms("windows")
def test_marker_with_a_live_delegate_is_handed_to_it_at_finish(tmp_path: Path) -> None:
    # A7 release rule: the owner deletes its claim unless a delegate still runs; then that
    # delegate becomes the owner (canonical body, started_at kept, no delegate line).
    pid_file = tmp_path / "delegate.pid"
    try:
        code, out, _, _result, home = _handoff(tmp_path, HANDOFF_DELEGATE=str(pid_file))
        assert code == 0, out
        marker = (home / ".hermes-update-in-progress").read_bytes().decode().split("\n")
        assert marker[0] == pid_file.read_text(encoding="utf-8-sig"), marker
        assert marker[2].startswith("ct:") and marker[3:] == [""], marker
    finally:
        if pid_file.exists():
            subprocess.run(
                ["taskkill", "/F", "/PID", pid_file.read_text(encoding="utf-8-sig")],
                capture_output=True,
                check=False,
            )
