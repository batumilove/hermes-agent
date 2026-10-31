"""Offline deadline and declared-configuration contract."""
import json
import subprocess
import sys
from unittest.mock import Mock
import pytest
from hermes_cli import honcho_config_acquisition as a
from hermes_cli import honcho_monitor as hm
from test_honcho_config_acquisition import packet
from test_honcho_monitor_metadata_integration import setup_offline

REAL_TRANSPORT = a.readonly_transport


def test_second_sample_receives_remaining_budget():
    ticks = iter([0, 0, 6, 6])
    transport = Mock(return_value=json.dumps(packet()))
    result = a.acquire_metadata(transport=transport, clock=lambda: 2000, monotonic=lambda: next(ticks))
    assert result['stable'] is True
    assert [c.kwargs['timeout'] for c in transport.call_args_list] == [15, 9]


def test_exhaustion_skips_second_transport():
    ticks = iter([0, 0, 15])
    transport = Mock(return_value=json.dumps(packet()))
    result = a.acquire_metadata(transport=transport, monotonic=lambda: next(ticks))
    assert transport.call_count == 1
    assert result['observations'] == {}


def test_transport_uses_exact_remaining_budget(monkeypatch):
    run = Mock(return_value='{}')
    monkeypatch.setattr(a, 'bounded_run', run)
    REAL_TRANSPORT('pass', timeout=.125)
    assert run.call_args.kwargs['timeout'] == .125


def test_timeout_main_keeps_health_warnings(monkeypatch, tmp_path, capsys):
    setup_offline(monkeypatch, tmp_path)
    transport = Mock(side_effect=TimeoutError('private-sentinel'))
    monkeypatch.setattr(a, 'readonly_transport', transport)
    monkeypatch.setenv('HONCHO_MONITOR_ALWAYS_PRINT', '1')
    assert hm.main() == 0
    report = capsys.readouterr().out
    assert 'DB stats probe failed' in report and 'API' in report
    assert 'Metadata: UNKNOWN' in report and 'private-sentinel' not in report
    assert transport.call_count == 1
    hm.curl_post_json.assert_not_called()


def test_declared_routes_all_env_entries_and_provenance():
    env = '\n'.join(['EMBEDDING_MODEL_CONFIG__MODEL=literal-model',
                     'EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=https://example.test/v1',
                     'DREAM_INDUCTION_MODEL_CONFIG__MODEL=dream-model',
                     'DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=https://dream.test/v1'])
    report = hm.format_report({'pipeline': hm.parse_pipeline_env(env)})
    assert 'Configured routes (declared/observed API-container env; NOT effective loaded configuration)' in report
    assert 'literal-model' in report and 'dream-model' in report
    assert 'Actual request routing: UNKNOWN' in report
    assert 'DB-inferred dimensions' in report


@pytest.mark.parametrize('stage', [{}, {'model': '\nforged', 'base_url': 'not-url'}, {'model': 'ok', 'base_url': 'not-url'}, {'model': 'ok', 'base_url': ''}])
def test_missing_malformed_route_is_unknown(stage):
    report = hm.format_report({'pipeline': {'embedding': stage}})
    assert 'Embedding: UNKNOWN (missing/malformed declared env route)' in report


def test_bounded_run_timeout_reaps_child():
    with pytest.raises(TimeoutError):
        a.bounded_run([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=.05)
