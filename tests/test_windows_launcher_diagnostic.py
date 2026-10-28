"""Offline observer controls; no Windows execution claimed."""
import importlib.util
import json
from pathlib import Path

SOURCE = Path(__file__).parents[1] / 'hermes_cli' / '_launcher_diagnostic.py'

def load():
    spec = importlib.util.spec_from_file_location('observer', SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_events_disabled_no_import_or_write(tmp_path, monkeypatch):
    observer = load()
    monkeypatch.delenv('HERMES_LAUNCHER_DIAGNOSTIC', raising=False)
    observer.event('test', filename='SECRET')
    assert not (tmp_path / 'launcher-diagnostic').exists()

def test_events_allowlist_and_cap(tmp_path, monkeypatch):
    import sys
    import types

    observer = load()
    monkeypatch.setenv('HERMES_LAUNCHER_DIAGNOSTIC', '1')
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_default_hermes_root=lambda: tmp_path))
    for _ in range(110):
        observer.event('mint-return', filename='hermes.exe', token='DO_NOT_RETAIN')
    records = list((tmp_path / 'launcher-diagnostic').glob('events-*.jsonl'))
    rows = [json.loads(line) for line in records[0].read_text().splitlines()]
    assert len(rows) == 96
    assert all('token' not in row for row in rows)
    assert 'DO_NOT_RETAIN' not in records[0].read_text()

def test_oversize_bytes_not_copied(tmp_path):
    observer = load()
    directory = tmp_path / 'bin'
    directory.mkdir()
    (directory / 'hermes.exe').write_bytes(b'x' * (observer.MAX_BYTES + 1))
    observer.snapshot(tmp_path, tmp_path / 'repo', 'before', enabled=True)
    assert not list((tmp_path / 'launcher-diagnostic').glob('*.bin'))
    assert json.loads((tmp_path / 'launcher-diagnostic' / 'before.json').read_text())['files'][0]['overflow']


def test_disabled_is_inert(tmp_path):
    observer = load()
    before = list(tmp_path.iterdir())
    observer.snapshot(tmp_path / 'absent', tmp_path / 'absent', 'before', enabled=False)
    assert list(tmp_path.iterdir()) == before

def test_bounded_bytes_and_hashes(tmp_path):
    observer = load()
    home = tmp_path / 'home'
    repo = home / 'hermes-agent'
    for directory in (home / 'bin', repo / '.hermes' / 'bin'):
        directory.mkdir(parents=True)
        (directory / 'hermes.exe').write_bytes(b'MZfixture')
        (directory / 'private-token.txt').write_text('SECRET')
    observer.snapshot(home, repo, 'before', enabled=True)
    data = (home / 'launcher-diagnostic' / 'before.json').read_text()
    assert 'SECRET' not in data and 'private-token' not in data
    receipt = json.loads(data)
    assert len(receipt['files']) == 2
    assert all(row['bytes'] == 9 and len(row['sha256']) == 64 for row in receipt['files'])
    assert len(list((home / 'launcher-diagnostic').glob('*.bin'))) == 2

def test_failure_retains_final_snapshot(tmp_path):
    observer = load()
    try:
        try:
            raise RuntimeError('original oracle')
        finally:
            observer.snapshot(tmp_path, tmp_path / 'repo', 'after', enabled=True)
    except RuntimeError as exc:
        assert str(exc) == 'original oracle'
    assert (tmp_path / 'launcher-diagnostic' / 'after.json').is_file()

def test_observer_write_failure_never_throws(tmp_path):
    observer = load()
    (tmp_path / 'launcher-diagnostic').write_text('blocked')
    observer.snapshot(tmp_path, tmp_path, 'after', enabled=True)
