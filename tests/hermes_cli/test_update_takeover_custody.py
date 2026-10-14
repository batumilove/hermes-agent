"""A surviving finish worker retains the same real lifecycle kernel lease."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap

import pytest

from gateway.lifecycle_lease import LifecycleLeaseBlocked, inspect_lifecycle_lease, reconcile_lifecycle_lease
from hermes_cli._subprocess_compat import pid_exists_stdlib
from tests.waiting import wait_until

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("exit_mode", ["complete", "killed", "cancelled"])
def test_finish_child_retains_lease_until_last_writer_exits(tmp_path, exit_mode):
    parent_dies = exit_mode == "killed"
    home, source = tmp_path / "home", tmp_path / "source"
    home.mkdir()
    package = source / "hermes_cli"
    package.mkdir(parents=True)
    fifo = tmp_path / "finish.fifo"
    os.mkfifo(fifo)
    (package / "update_finish.py").write_text(textwrap.dedent(f'''\
        import json, os
        from pathlib import Path
        lock = Path({str(home)!r}) / '.lifecycle_transaction_lease.lock'
        inode = lock.stat().st_ino
        held = [int(p.name) for p in Path('/proc/self/fd').iterdir()
                if p.exists() and p.stat().st_ino == inode]
        assert held, 'finish child did not inherit lifecycle custody'
        print(json.dumps({{'pid':os.getpid(), 'fds':held}}), flush=True)
        with open({str(fifo)!r}) as release:
            assert release.readline().strip() == 'finish'
        '''), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=fixture", "-c",
                    "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "-qm", "selected source"], check=True, capture_output=True)
    context, result = tmp_path / "request.json", tmp_path / "result.json"
    context.write_text("{}", encoding="utf-8")
    program = textwrap.dedent(f'''\
        import os, sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT)!r})
        from hermes_cli.lifecycle_coordination import cli_update_transaction
        from hermes_cli.update_lock import UpdateLock
        from hermes_cli._update_takeover import _run_finish_child
        home, source = Path({str(home)!r}), Path({str(source)!r})
        try:
            with UpdateLock(install_root=source) as lock:
                assert lock.acquired
                with cli_update_transaction(home, source) as lease:
                    assert _run_finish_child({{'root':str(source)}}, Path({str(context)!r}),
                        Path({str(result)!r}), Path(sys.executable), dict(os.environ),
                        lifecycle_fds=lease.custody_fds()) == 0
        except KeyboardInterrupt:
            print('cancelled', flush=True)
            raise SystemExit(0)
        print('released', flush=True)
        ''')
    env = {**os.environ, "HERMES_HOME": str(home)}
    owner = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", program],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    writer = None
    worker_pid = None
    try:
        line = owner.stdout.readline()
        assert line, owner.communicate(timeout=10)[1]
        worker = json.loads(line)
        worker_pid = worker["pid"]
        writer = fifo.open("w", encoding="utf-8")
        observed = inspect_lifecycle_lease(home=home)
        assert observed["status"] == "active" and worker["fds"]
        if exit_mode == "cancelled":
            owner.send_signal(signal.SIGINT)
            out, error = owner.communicate(timeout=10)
            assert owner.returncode == 0 and out.strip() == "cancelled", error
            assert not pid_exists_stdlib(worker_pid), "lease released before its cancelled writer exited"
            assert inspect_lifecycle_lease(home=home)["status"] == "absent"
            return
        if parent_dies:
            owner.send_signal(signal.SIGKILL)
            owner.wait(timeout=10)
            assert pid_exists_stdlib(worker_pid)
            observed = inspect_lifecycle_lease(home=home)
            assert observed["status"] == "active"
            with pytest.raises(LifecycleLeaseBlocked, match="active"):
                reconcile_lifecycle_lease(home=home, expected_metadata_sha256=observed["metadata_sha256"],
                                          expected_owner_token=observed["metadata"]["owner_token"],
                                          expected_purpose=observed["metadata"]["purpose"])
        writer.write("finish\n")
        writer.close()
        writer = None
        assert wait_until(lambda: not pid_exists_stdlib(worker_pid), timeout=10)
        if parent_dies:
            orphan = inspect_lifecycle_lease(home=home)
            assert orphan["status"] == "orphaned"
            assert orphan["metadata_sha256"] == observed["metadata_sha256"]
            assert reconcile_lifecycle_lease(home=home, expected_metadata_sha256=orphan["metadata_sha256"],
                                             expected_owner_token=orphan["metadata"]["owner_token"],
                                             expected_purpose=orphan["metadata"]["purpose"])["status"] == "reconciled"
        else:
            out, error = owner.communicate(timeout=10)
            assert owner.returncode == 0, error
            assert out.strip() == "released"
        assert inspect_lifecycle_lease(home=home)["status"] == "absent"
    finally:
        if writer is not None:
            writer.close()
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)
        if worker_pid is not None and pid_exists_stdlib(worker_pid):
            os.kill(worker_pid, signal.SIGKILL)
