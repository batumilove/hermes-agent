"""Telemetry path configuration follows the same profile loader and schema as the CLI."""
from pathlib import Path

from evidence import provider_telemetry_fixed as telemetry
from hermes_cli.config import _validate_config_key
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_metrics_path_is_registered_and_each_profile_reads_its_own_yaml(tmp_path):
    assert _validate_config_key('provider_telemetry.metrics_file')[0]
    paths = []
    for name in ['alpha', 'beta']:
        home = tmp_path / name
        home.mkdir()
        target = home / 'custom.prom'
        (home / 'config.yaml').write_text(
            f'provider_telemetry:\n  metrics_file: {target}\n', encoding='utf-8',
        )
        token = set_hermes_home_override(home)
        try:
            paths.append(telemetry._metrics_file())
        finally:
            reset_hermes_home_override(token)
    assert paths == [tmp_path / 'alpha/custom.prom', tmp_path / 'beta/custom.prom']


def test_empty_path_uses_owning_profile_without_creating_metric_state(tmp_path):
    (tmp_path / 'config.yaml').write_text('{}\n', encoding='utf-8')
    token = set_hermes_home_override(tmp_path)
    try:
        path = telemetry._metrics_file()
    finally:
        reset_hermes_home_override(token)
    assert path == Path(tmp_path, 'state/prometheus/hermes_provider_telemetry.prom')
    assert not path.exists()
