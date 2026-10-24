"""An unreaped exited process cannot retain durable delivery or finalizer ownership."""
import subprocess
import sys

import psutil
import pytest

from gateway.delivery_ledger import _owner_alive
from gateway.status import get_process_start_time
from hermes_state import SessionDB
from tests.waiting import wait_until


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("owner_alive", [_owner_alive, SessionDB._post_response_owner_alive])
def test_unreaped_owner_cannot_hold_durable_work(owner_alive):
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        started = get_process_start_time(child.pid)
        assert started is not None
        assert owner_alive(child.pid, started)
        child.terminate()
        wait_until(lambda: psutil.Process(child.pid).status() == psutil.STATUS_ZOMBIE,
                   timeout=5, what="child to exit without being reaped")
        assert not owner_alive(child.pid, started)
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
