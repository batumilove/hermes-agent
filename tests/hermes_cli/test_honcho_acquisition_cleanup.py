"""Native process cleanup contracts for bounded metadata collection."""
import os
import subprocess
import sys

import pytest

from hermes_cli.honcho_config_acquisition import bounded_run


@pytest.mark.platforms("posix")
def test_cleanup_wait_is_bounded(monkeypatch):
    original = subprocess.Popen.wait
    waits = []

    def wait(self, timeout=None):
        waits.append(timeout)
        return original(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    assert bounded_run([sys.executable, "-c", "print('ok')"]) == "ok\n"
    assert waits and all(value is not None and value > 0 for value in waits)


@pytest.mark.platforms("posix")
def test_timeout_reaps_direct_child(tmp_path):
    pidfile = tmp_path / "pid"
    script = "import os,pathlib,threading;pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));threading.Event().wait()"
    with pytest.raises(TimeoutError):
        bounded_run([sys.executable, "-c", script], timeout=2)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.platforms("windows")
def test_unsupported_pipe_selector_fails_before_spawn(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported transport spawned a child")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(OSError):
        bounded_run([sys.executable, "-c", "print('unsafe')"])
