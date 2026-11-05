"""Final bootstrap cleanup must survive independent resource failures (PR #369)."""

import asyncio

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['control', 'cancel', 'pid'])
async def test_final_cleanup_attempts_identity_after_a_cleanup_error(monkeypatch, failure):
    from gateway import run_bootstrap, run_runtime, status
    from gateway.config import GatewayConfig
    from gateway.control_socket import GatewayControlServer
    from gateway.run_bootstrap import start_gateway
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    home.chmod(0o700)
    order = []
    original_start_threads = run_bootstrap._start_gateway_start_cron_and_housekeeping
    runtime_threads = []
    def start_threads(runner):
        result = original_start_threads(runner)
        runtime_threads.append(result)
        return result
    original_stop = GatewayControlServer.stop
    original_remove = status.remove_pid_file
    original_release = status.release_gateway_runtime_lock
    from pathlib import Path
    original_unlink = Path.unlink
    async def ready_to_stop(runner):
        return None
    async def stop_control(server):
        await original_stop(server)
        if failure == 'control':
            raise RuntimeError('injected control cleanup failure')
        if failure == 'cancel':
            raise asyncio.CancelledError()
    def remove_pid():
        order.append('pid')
        original_remove()
    def unlink(path, *args, **kwargs):
        if failure == 'pid' and path == home / 'gateway.pid':
            raise OSError('injected PID unlink failure')
        return original_unlink(path, *args, **kwargs)
    def release_lock(**kwargs):
        order.append('lock')
        result = original_release(**kwargs)
        if failure == 'cancel':
            from gateway.runtime_ownership import process_ownership
            assert result is False
            assert process_ownership.owns(home)
        return result
    monkeypatch.setattr(run_runtime, 'wait_gateway_runtime', ready_to_stop)
    monkeypatch.setattr(run_bootstrap, '_start_gateway_start_cron_and_housekeeping', start_threads)
    monkeypatch.setattr(GatewayControlServer, 'stop', stop_control)
    monkeypatch.setattr(status, 'remove_pid_file', remove_pid)
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(status, 'release_gateway_runtime_lock', release_lock)
    try:
        if failure in ('control', 'cancel'):
            error = RuntimeError if failure == 'control' else asyncio.CancelledError
            with pytest.raises(error):
                await asyncio.wait_for(start_gateway(GatewayConfig()), 30)
        else:
            assert await asyncio.wait_for(start_gateway(GatewayConfig()), 30)
        assert order == ['pid', 'lock']
    finally:
        for stop, _provider, cron_thread, housekeeping_thread in runtime_threads:
            stop.set()
            for thread in (cron_thread, housekeeping_thread):
                if thread is not None:
                    await asyncio.to_thread(thread.join, 5)
                    assert not thread.is_alive()
        monkeypatch.setattr(Path, 'unlink', original_unlink)
        original_remove()
        original_release()

@pytest.mark.asyncio
@pytest.mark.platforms('posix')
async def test_pending_status_writer_keeps_native_lock_without_blocking_final_cleanup(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    import threading
    from gateway import status
    from gateway.run_bootstrap_cleanup import finalize_runtime_identity
    from gateway import runtime_ownership
    process_ownership = runtime_ownership.ProfileOwnership()
    monkeypatch.setattr(runtime_ownership, 'process_ownership', process_ownership)

    home = tmp_path / 'runtime'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    entered, release = threading.Event(), threading.Event()
    def write(path, payload):
        entered.set()
        assert release.wait(15)
        status._write_json_file(path, payload)
    writer = status._RuntimeStatusWriter(write_fn=write)
    original_flush = writer.flush
    def flush(*, timeout):
        assert timeout == 0.0, 'final cleanup must not wait behind a stalled status commit'
        return original_flush(timeout=timeout)
    monkeypatch.setattr(writer, 'flush', flush)
    monkeypatch.setattr(status, '_runtime_status_writer', writer)
    process_ownership.reserve([home])
    probe = '''
from gateway.status import acquire_gateway_runtime_lock, release_gateway_runtime_lock
claimed = acquire_gateway_runtime_lock()
print('CLAIMED', claimed)
if claimed:
    release_gateway_runtime_lock()
'''
    def contender():
        result = subprocess.run([sys.executable, '-c', probe], env=os.environ.copy(),
            capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    try:
        writer.submit(home / 'gateway_state.json', {'synthetic': True})
        assert entered.wait(5)
        await finalize_runtime_identity(None)
        assert process_ownership.owns(home)
        assert contender() == 'CLAIMED False'
    finally:
        release.set()
        assert original_flush(timeout=5)
        assert status.release_gateway_runtime_lock(timeout=0.0)
    assert contender() == 'CLAIMED True'
