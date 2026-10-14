"""Native control identity, pre-stop debt and old-gateway takeover boundaries."""
from __future__ import annotations

from datetime import datetime, UTC
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from hermes_cli import update_pause_record as records
from hermes_cli import update_takeover_pause as pause

ROOT = Path(__file__).resolve().parents[2]
OLD_SHA = "a" * 40


@pytest.fixture
def gateway(tmp_path, monkeypatch, request):
    """A real process serves the historical one-request control protocol."""
    drain_timeout = getattr(request, "param", 2)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    install = tmp_path / "install"
    script = install / "hermes_cli/main.py"
    script.parent.mkdir(parents=True)
    script.write_text(textwrap.dedent(f'''\
        import asyncio, json, os, sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT)!r})
        from gateway.control_socket import GatewayControlServer
        home = Path({str(home)!r})
        async def main():
            loop = asyncio.get_running_loop()
            done = asyncio.Event()
            def identify():
                start = int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
                return {{'kind':'hermes-gateway', 'pid':os.getpid(), 'start_time':start,
                         'hermes_home':str(home), 'supervisor':'manual', 'code_sha':{OLD_SHA!r}}}
            def drain():
                (home/'pause-request').write_text('accepted')
                loop.call_soon_threadsafe(lambda: loop.call_later(0.1, done.set))
                return {{'pid':os.getpid(), 'pausing':True, 'drain_timeout':json.loads({json.dumps(drain_timeout)!r})}}
            server = GatewayControlServer(home, verb_handlers={{'identify':identify, 'pause-for-update':drain}})
            assert await server.start()
            print('ready', flush=True)
            await done.wait()
            await server.stop()
        asyncio.run(main())
        '''), encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-I", "-B", str(script), "gateway", "run", "--port", "8123"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        request = {"root": str(install), "home": str(home), "no_gateway_restart": False,
                   "receipt": {"started_at": datetime.now(UTC).isoformat()},
                   "plan": {"expected_sha": OLD_SHA, "runtimes": [
                       {"kind": "gateway", "profile": "default", "pid": child.pid,
                        "supervisor": "manual", "code_sha": OLD_SHA}]}}
        yield child, home, request
    finally:
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=10)


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("gateway", [0, 2], indirect=True)
def test_pause_proves_exit_and_retains_replayable_restart_debt(gateway):
    child, home, request = gateway
    token = pause.pause_captured_gateways(request)
    child.wait(timeout=10)
    assert child.returncode == 0
    assert (home / "pause-request").read_text(encoding="utf-8") == "accepted"
    saved = records.read()["token"]
    assert saved["stop_sent"] == [str(child.pid)]
    assert saved["resume_needed"] and saved["unmapped_pids"] == [child.pid]
    assert saved["unmapped"][0]["takeover_args"] == ["gateway", "run", "--port", "8123"]
    assert saved["identities"][str(child.pid)].startswith("ct:")
    assert token is request["windows_resume"]


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("gateway", [-1, float("inf"), float("nan"), 3601, None, False], indirect=True)
def test_invalid_native_pause_budget_retains_debt(gateway):
    child, home, request = gateway
    with pytest.raises(pause.TakeoverPauseBlocked, match="pause"):
        pause.pause_captured_gateways(request)
    assert (home / "pause-request").exists()
    saved = records.read()["token"]
    assert saved["resume_needed"]
    assert saved["stop_requested"] == saved["stop_sent"] == [str(child.pid)]


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("mismatch", ["source", "supervisor", "peer", "birth", "second-runtime"])
def test_unproven_set_never_stops_a_captured_gateway(gateway, mismatch):
    child, home, request = gateway
    row = request["plan"]["runtimes"][0]
    changes = {
        "source": lambda: row.update(code_sha="b" * 40),
        "supervisor": lambda: row.update(supervisor="systemd"),
        "peer": lambda: row.update(pid=os.getpid()),
        "birth": lambda: request["receipt"].update(started_at="1970-01-01T00:00:00+00:00"),
        "second-runtime": lambda: request["plan"]["runtimes"].append({**row, "supervisor": "launchd"}),
    }
    changes[mismatch]()
    with pytest.raises(pause.TakeoverPauseBlocked):
        pause.pause_captured_gateways(request)
    assert child.poll() is None
    assert not (home / "pause-request").exists()
    assert records.read() is None


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
def test_original_no_restart_intent_stops_no_gateway(gateway):
    child, home, request = gateway
    request["no_gateway_restart"] = True
    assert pause.pause_captured_gateways(request) is None
    assert child.poll() is None
    assert not (home / "pause-request").exists()
    assert records.read() is None


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
def test_mixed_home_set_stops_neither_actual_gateway(gateway):
    child, home, request = gateway
    other_home = home / "profiles" / "secondary"
    other_home.mkdir(parents=True)
    script = Path(request["root"]) / "hermes_cli/main.py"
    script.write_text(script.read_text(encoding="utf-8").replace(repr(str(home)), repr(str(other_home))), encoding="utf-8")
    other = subprocess.Popen([sys.executable, "-I", "-B", str(script), "gateway", "run"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "ready"
        request["receipt"]["started_at"] = datetime.now(UTC).isoformat()
        request["plan"]["runtimes"].append({"kind": "gateway", "profile": "secondary",
                                           "pid": other.pid, "supervisor": "manual", "code_sha": OLD_SHA})
        with pytest.raises(pause.TakeoverPauseBlocked, match="outside this lifecycle lease"):
            pause.pause_captured_gateways(request)
        assert child.poll() is None and other.poll() is None
        assert not (home / "pause-request").exists()
        assert not (other_home / "pause-request").exists()
        assert records.read() is None
    finally:
        if other.poll() is None:
            other.terminate()
        other.communicate(timeout=10)


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
def test_cancelled_wait_keeps_exact_pre_stop_custody(gateway, monkeypatch):
    child, home, request = gateway
    query = pause._query
    def interrupt_after_ack(home, pid, verb):
        reply = query(home, pid, verb)
        if verb == "pause-for-update":
            raise KeyboardInterrupt
        return reply
    # Cancel only after the native stop ACK, never during the record mutex's
    # own retry wait (all modules share the same time module).
    monkeypatch.setattr(pause, "_query", interrupt_after_ack)
    with pytest.raises(KeyboardInterrupt):
        pause.pause_captured_gateways(request)
    assert (home / "pause-request").exists()
    saved = records.read()["token"]
    assert saved["stop_requested"] == saved["stop_sent"] == [str(child.pid)]
    assert saved["resume_needed"] and saved["unmapped"][0]["identity"]


def test_historical_request_preserves_no_restart_flag():
    from hermes_cli.update_handoff import _takeover_request

    request = _takeover_request({}, ["--yes", "--no-gateway-restart"])
    assert request["no_gateway_restart"] is True
    assert _takeover_request({}, ["--yes"])["no_gateway_restart"] is False


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
def test_takeover_provisions_only_after_captured_gateway_exits(gateway, monkeypatch):
    """The real preparation entrypoint must quiesce before its first tool mutation."""
    from hermes_cli import _update_takeover, gitlock, update_stage
    from pm import client

    child, _, request = gateway
    request["update_id"] = "test-takeover"
    monkeypatch.setattr(update_stage, "ensure_panel", lambda _root: None)
    monkeypatch.setattr(gitlock, "convert_treeless_checkout_first", lambda _root: None)
    monkeypatch.setattr(_update_takeover, "_arm_fleet_obligation", lambda _root: None)
    def provision():
        assert child.poll() is not None, "tool provisioning began while the old process could load pulled code"
        assert records.read()["token"]["resume_needed"]
        raise RuntimeError("provisioner seam reached after quiescence")
    monkeypatch.setattr(client, "ensure_tools_for_sync", provision)
    with pytest.raises(RuntimeError, match="provisioner seam reached"):
        _update_takeover.prepare(request)


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("restart_update", [False, True])
def test_takeover_retains_lease_but_delegated_cli_can_acquire_its_own(tmp_path, monkeypatch, restart_update):
    from hermes_cli import _update_takeover

    home, source = tmp_path / "home", tmp_path / "source"
    home.mkdir()
    source.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    subprocess.run(["git", "init", "-q", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=fixture", "-c",
                    "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "--allow-empty", "-qm", "selected source"], check=True, capture_output=True)
    context, result = tmp_path / "request.json", tmp_path / "result.json"
    context.write_text(json.dumps({"root": str(source), "receipt": {}, "restart_update": restart_update}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["takeover", str(context), str(result)])
    def prepare(_request):
        assert (home / ".lifecycle_transaction_lease.json").is_file()
        return Path(sys.executable), dict(os.environ)
    monkeypatch.setattr(_update_takeover, "prepare", prepare)
    def finish(*_args, lifecycle_fds):
        assert bool(lifecycle_fds) is (not restart_update)
        command = textwrap.dedent(f'''\
            import sys
            from pathlib import Path
            sys.path.insert(0, {str(ROOT)!r})
            from hermes_cli.lifecycle_coordination import cli_update_transaction
            with cli_update_transaction(Path({str(home)!r}), Path({str(source)!r})):
                Path({str(result)!r}).write_text('owned by delegated CLI')
            ''')
        child = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", command],
                               text=True, capture_output=True, timeout=10, check=False)
        assert child.returncode == (0 if restart_update else 2), child.stderr
        assert result.exists() is restart_update
        return 0
    monkeypatch.setattr(_update_takeover, "_run_finish_child", finish)
    assert _update_takeover.main() == 0
    assert not (home / ".lifecycle_transaction_lease.json").exists()
