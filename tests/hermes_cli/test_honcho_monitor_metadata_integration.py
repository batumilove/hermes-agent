"""Real collection/render/CLI wiring, exclusively injected offline side effects."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from hermes_cli import honcho_monitor as hm
from hermes_cli import honcho_config_acquisition as acquisition


def packet():
    return {"hostname": "honcho", "containers": {name: {
        "container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
        "started_at": "2026-10-24T05:24:12Z", "running": True,
        "source_manifest": {"src/config.py": "c" * 64},
        "parser_manifest": {"pydantic_settings/main.py": "d" * 64},
        "source_presence": {"/app/config.toml": False},
        "env_input": {}, "unsupported_input": False,
    } for name in ("api", "deriver")}}


def setup_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(hm, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(hm, "ssh", lambda *a, **k: "honcho-api-1 Up\nhoncho-deriver-1 Up\nhoncho-database-1 Up\nhoncho-redis-1 Up" if "compose ps" in a[0] else "")
    monkeypatch.setattr(hm, "latest_local_message_timestamp", lambda: None)
    monkeypatch.setattr(hm, "curl_post_json", Mock(side_effect=AssertionError("provider forbidden")))


def test_snapshot_and_main_render_metadata_once(monkeypatch, tmp_path, capsys):
    setup_offline(monkeypatch, tmp_path)
    transport = Mock(return_value=json.dumps(packet()))
    monkeypatch.setattr(acquisition, "readonly_transport", transport)
    monkeypatch.setenv("HONCHO_MONITOR_ALWAYS_PRINT", "1")
    assert hm.main() == 0
    text = capsys.readouterr().out
    assert "Metadata: observed at" in text
    assert "api=" in text and "deriver=" in text
    assert "Inspected configuration: UNKNOWN" in text
    assert "Actual request routing: UNKNOWN" in text
    assert "All nominal" not in text
    assert "API" in text
    assert "DB stats probe failed" in text
    assert transport.call_count == 2  # one acquisition, two independent samples
    hm.curl_post_json.assert_not_called()


@pytest.mark.parametrize("raw", ["{", "{}", "[]"])
def test_bad_metadata_isolated_from_service_report(monkeypatch, tmp_path, raw):
    setup_offline(monkeypatch, tmp_path)
    monkeypatch.setattr(acquisition, "readonly_transport", lambda _, **kwargs: raw)
    snapshot, _ = hm.collect_snapshot()
    assert "Metadata: UNKNOWN" in hm.format_report(snapshot)
    assert "DB stats probe failed" in hm.format_report(snapshot)


def test_consumer_ttl_and_missing_record(monkeypatch, tmp_path):
    setup_offline(monkeypatch, tmp_path)
    monkeypatch.setattr(acquisition, "readonly_transport", lambda _, **kwargs: json.dumps(packet()))
    snapshot, _ = hm.collect_snapshot()
    assert snapshot.metadata["stable"] is True
    stale = hm.datetime.fromtimestamp(snapshot.metadata["collected_at"] + 301, hm.UTC)
    assert "Metadata: UNKNOWN" in hm.format_report(snapshot, now=stale)
    assert "Metadata: UNKNOWN" in hm.format_report({})


def test_acquisition_exception_does_not_hide_health(monkeypatch, tmp_path):
    setup_offline(monkeypatch, tmp_path)
    monkeypatch.setattr(acquisition, "acquire_metadata", Mock(side_effect=RuntimeError("private-sentinel")))
    snapshot, _ = hm.collect_snapshot()
    report = hm.format_report(snapshot)
    assert "Metadata: UNKNOWN" in report
    assert "DB stats probe failed" in report
    assert "private-sentinel" not in report


def test_exact_module_cli_offline_subprocess(tmp_path):
    # Test-only harness, no runtime option; real __main__, collection and renderer.
    root = Path(__file__).resolve().parents[2]
    code = '''import runpy, os
from pathlib import Path
from unittest.mock import patch
import hermes_cli.honcho_config_acquisition as a
os.environ['HONCHO_MONITOR_ALWAYS_PRINT']='1'
os.environ['HONCHO_MONITOR_STATE']=os.environ['FIXTURE_STATE']
def forbidden(*args, **kwargs): raise AssertionError('external effect forbidden')
with patch('subprocess.run', return_value=__import__('subprocess').CompletedProcess([], 1, '', 'offline')), patch('sqlite3.connect', side_effect=__import__('sqlite3').OperationalError('offline')), patch.object(a, 'readonly_transport', return_value='{}'):
    runpy.run_module('hermes_cli.honcho_monitor', run_name='__main__')
'''
    import os
    env = dict(os.environ, PYTHONPATH=str(root), FIXTURE_STATE=str(tmp_path / "state.json"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=root, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert "Metadata: UNKNOWN" in result.stdout
    assert "Inspected configuration: UNKNOWN" in result.stdout
    assert "Actual request routing: UNKNOWN" in result.stdout
