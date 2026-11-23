"""Large-store capacity using disposable metadata and real OS resource limits."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from tests.hermes_cli.test_insights_metadata_read import SESSION
from tests.hermes_cli.test_insights_refresh_stage import refresh, valid_source

pytest_plugins = ['tests.hermes_cli.test_insights_metadata_read']


@pytest.mark.platforms('linux')
@pytest.mark.parametrize('memory_mib, expected', [(1024, 0), (64, 1)])
def test_large_store_under_os_budget(databases, tmp_path, memory_mib, expected):
    valid_source(databases)
    source = databases[1]
    source.execute('delete from messages')
    source.executemany('insert into sessions select ' + ','.join(
        '?' if column == 'id' else column
        for column in SESSION
    ) + " from sessions where id='s'", ((f's{i}',) for i in range(1, 12416)))
    source.executemany('insert into messages values (?,?,?,?,?,?,?,?,?,?,?)', (
        (i, 's', 'assistant' if i % 2 else 'user', 'terminal' if i % 3 == 0 else None,
         1700000000 + i, 128, 'stop', 1, 0, 'private payload ' * 16, '[]')
        for i in range(1, 683836)))
    source.commit()
    stage = tmp_path / 'large'
    script = '''
import argparse, resource, sys
from hermes_cli.insights_refresh_stage import register_cli
limit = int(sys.argv.pop(1)) * 1024**2
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
resource.setrlimit(resource.RLIMIT_FSIZE, (256*1024**2, 256*1024**2))
p = argparse.ArgumentParser()
register_cli(p.add_subparsers())
a = p.parse_args()
raise SystemExit(a.func(a))
'''
    result = subprocess.run([sys.executable, '-c', script, str(memory_mib), 'insights-refresh-stage',
        '--stage-dir', str(stage), '--schema-sha256', databases[3],
        '--max-rows', '1000000', '--max-bytes', str(256 * 1024**2), '--seconds', '120'],
        env={**os.environ, 'HERMES_HOME': str(tmp_path)}, capture_output=True, text=True,
        timeout=90, check=False)
    assert result.returncode == expected, result.stderr
    if expected:
        assert not stage.exists()
        assert not result.stdout
        assert result.stderr.strip() == 'Insights private stage refresh refused'
        return
    report = json.loads(result.stdout)
    assert report['messages'] == 683835 and report['sessions'] == 12416
    with sqlite3.connect(stage / 'metadata-summary.db') as target:
        assert target.execute('select count(*) from summary_message_metadata').fetchone() == (683835,)
        assert target.execute('select sum(count) from summary_session_roles').fetchone() == (683835,)


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('options', [
    {'max_rows': 2000001}, {'max_bytes': 512 * 1024**2 + 1},
    {'seconds': 301}, {'seconds': 'nan'}, {'seconds': 'inf'},
    {'max_rows': 0}, {'max_bytes': 0}, {'seconds': -1},
])
def test_finite_capacity_limits(databases, tmp_path, options):
    valid_source(databases)
    stage = tmp_path / 'refused'
    assert refresh(databases, stage, **options) == 1
    assert not stage.exists()


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('failure', [MemoryError, TimeoutError, 'deadline'])
def test_serialization_budget_failure_rolls_back(databases, tmp_path, monkeypatch, failure):
    from hermes_cli import insights_refresh_stage as module
    valid_source(databases)
    original = module.sqlite3.connect

    class LimitedConnection(sqlite3.Connection):
        def serialize(self, *args, **kwargs):
            if failure == 'deadline':
                payload = super().serialize(*args, **kwargs)
                monkeypatch.setattr(module.time, 'monotonic', lambda: float('inf'))
                return payload
            raise failure('runtime budget')

    def connect(*args, **kwargs):
        if args[0] == ':memory:':
            kwargs['factory'] = LimitedConnection
        return original(*args, **kwargs)

    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    stage = tmp_path / 'failed'
    assert refresh(databases, stage) == 1
    assert not stage.exists()
