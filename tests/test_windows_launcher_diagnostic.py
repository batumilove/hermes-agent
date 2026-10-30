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
    assert rows[-1]['kind'] == 'overflow'
    assert rows[-1]['truncated'] is True
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

def test_maker_oneshot_not_consumed(tmp_path, monkeypatch, native_supplier):
    import shutil
    from hermes_cli import _launchers as launchers
    from tests.native_supplier import windows_unit_maker

    python = tmp_path / 'python.exe'
    python.touch()
    shutil.copytree(native_supplier, tmp_path / 'Lib/site-packages/distlib')
    real = windows_unit_maker(python)
    consumed, calls = [], []
    class Maker(real):
        def make(self, *args):
            paths = super().make(*args)
            calls.append(paths)
            def once():
                consumed.append(True)
                yield from paths
            return once()

    monkeypatch.setattr(launchers, '_is_windows', lambda: True)
    monkeypatch.setattr(launchers, '_store_script_maker', lambda selected: Maker if selected == python else None)
    for enabled in ('0', '1'):
        out = tmp_path / enabled
        out.mkdir()
        monkeypatch.setenv('HERMES_LAUNCHER_DIAGNOSTIC', enabled)
        result = launchers.mint_launcher('hermes', tmp_path, out, python, None)
        assert result == out / 'hermes.exe'
        assert launchers._valid_native(result, python, launchers._launcher_script('hermes', tmp_path, None))
    assert len(calls) == 2
    assert consumed == []


def test_maker_throwing_observer_preserves_result(tmp_path, monkeypatch, native_supplier):
    import shutil
    from hermes_cli import _launchers as launchers
    from hermes_cli import _launcher_diagnostic as observer
    from tests.native_supplier import windows_unit_maker

    python = tmp_path / 'python.exe'
    python.touch()
    shutil.copytree(native_supplier, tmp_path / 'Lib/site-packages/distlib')
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError('DO_NOT_RETAIN')
    monkeypatch.setenv('HERMES_LAUNCHER_DIAGNOSTIC', '1')
    monkeypatch.setattr(observer, 'event', fail)
    maker = windows_unit_maker(python)
    monkeypatch.setattr(launchers, '_is_windows', lambda: True)
    monkeypatch.setattr(launchers, '_store_script_maker', lambda selected: maker if selected == python else None)
    result = launchers.mint_launcher('hermes', tmp_path, tmp_path, python, None)
    assert result == tmp_path / 'hermes.exe'
    assert attempts
    assert launchers._valid_native(result, python, launchers._launcher_script('hermes', tmp_path, None))


def test_extensionless_both_bins(tmp_path):
    observer = load()
    repo = tmp_path / 'repo'
    for directory in (tmp_path / 'bin', repo / '.hermes' / 'bin'):
        directory.mkdir(parents=True)
        for name in ('hermes', 'hermes-acp'):
            (directory / name).write_bytes(name.encode())
    observer.snapshot(tmp_path, repo, 'before', enabled=True)
    rows = json.loads((tmp_path / 'launcher-diagnostic' / 'before.json').read_text())['files']
    assert {(r['directory'], r['name']) for r in rows} == {(d, n) for d in ('user-bin', 'checkout-bin') for n in ('hermes', 'hermes-acp')}


def test_store_resource_bytes_and_outcomes(tmp_path, monkeypatch):
    import hashlib
    import sys
    import types
    observer = load()
    monkeypatch.setenv('HERMES_LAUNCHER_DIAGNOSTIC', '1')
    monkeypatch.setitem(sys.modules, 'hermes_constants', types.SimpleNamespace(get_default_hermes_root=lambda: tmp_path))
    selected, own = tmp_path / 'selected', tmp_path / 'own'
    for directory, payload in ((selected, b'{"packages":{}}'), (own, b'{"packages":{"python":{}}}')):
        directory.mkdir()
        (directory / 'facts.json').write_bytes(payload)
    monkeypatch.setitem(sys.modules, 'pm.environments', types.SimpleNamespace(store_root=lambda repo, honor_runtime_override=True: selected if honor_runtime_override else own))
    python = tmp_path / 'python' / 'python.exe'
    package = python.parent / 'Lib/site-packages/pip/_vendor/distlib'
    package.mkdir(parents=True)
    (package / '__init__.py').write_bytes(b'first')
    (package / 'scripts.py').write_bytes(b'second')
    (package / 'resources.py').mkdir()  # bounded read error, not exception text
    (package / 't64.exe').write_bytes(b'x' * (observer.MAX_BYTES + 1))
    observer.store_inventory(tmp_path, python)
    dest = tmp_path / 'launcher-diagnostic'
    rows = [json.loads(line) for file in dest.glob('events-*') for line in file.read_text().splitlines()]
    resources = {Path(row['source']).name: row for row in rows if row['kind'] == 'vendored-resource'}
    assert resources['__init__.py']['sha256'] == hashlib.sha256(b'first').hexdigest()
    assert resources['scripts.py']['sha256'] != resources['__init__.py']['sha256']
    assert resources['resources.py']['status'] == 'read-error'
    assert resources['t64.exe']['status'] == 'oversize'
    assert resources['t32.exe']['status'] == 'missing'
    assert (dest / resources['scripts.py']['resource']).read_bytes() == b'second'
    facts = [row for row in rows if row['kind'].startswith('facts-')]
    assert len(facts) == 2 and facts[0]['sha256'] != facts[1]['sha256']
    assert all('exception_message' not in row for row in rows)


def test_retirement_completion_class_only(tmp_path, monkeypatch):
    from hermes_cli import _launchers as launchers
    rows = []
    monkeypatch.setattr(launchers, '_diagnostic', lambda kind, **fields: rows.append({'kind': kind, **fields}))
    exe = tmp_path / 'hermes.exe'
    exe.write_bytes(b'MZ')
    assert launchers._retire_exe(exe)
    assert any(r['kind'] == 'retire-unlink-success' for r in rows)
    rows.clear()
    def blocked(*args, **kwargs):
        raise PermissionError('DO_NOT_RETAIN')
    monkeypatch.setattr(Path, 'unlink', blocked)
    monkeypatch.setattr(launchers.os, 'replace', blocked)
    assert not launchers._retire_exe(exe)
    assert [r['kind'] for r in rows] == ['retire-enter', 'retire-unlink-failure', 'retire-rename', 'retire-rename-failure']
    assert 'DO_NOT_RETAIN' not in str(rows)


def test_early_store_refusal_observed(tmp_path, monkeypatch):
    from hermes_cli import _launchers as launchers
    rows = []
    monkeypatch.setattr(launchers, '_diagnostic', lambda kind, **fields: rows.append({'kind': kind, **fields}))
    assert launchers._store_script_maker(tmp_path / 'python.exe') is None
    assert rows == [{'kind': 'store-maker-refusal', 'available': False, 'reason': 'invalid-selected-supplier'}]


def test_retirement_rename_and_aside_outcomes(tmp_path, monkeypatch):
    from hermes_cli import _launchers as launchers
    rows = []
    monkeypatch.setattr(launchers, '_diagnostic', lambda kind, **fields: rows.append({'kind': kind, **fields}))
    original = Path.unlink
    exe = tmp_path / 'hermes.exe'
    for aside_blocked in (False, True):
        exe.write_bytes(b'MZ')
        def unlink(path, *args, **kwargs):
            if path == exe or aside_blocked:
                raise PermissionError('DO_NOT_RETAIN')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', unlink)
        rows.clear()
        assert launchers._retire_exe(exe)
        assert any(r['kind'] == 'retire-rename-success' for r in rows)
        assert rows[-1]['kind'] == ('retire-aside-unlink-failure' if aside_blocked else 'retire-aside-unlink-success')
        assert 'DO_NOT_RETAIN' not in str(rows)


def test_ambient_import_failures_class_only(monkeypatch):
    import builtins
    from hermes_cli import _launchers as launchers
    rows = []
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in ('distlib.scripts', 'pip._vendor.distlib.scripts'):
            raise ImportError('DO_NOT_RETAIN')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(launchers, '_diagnostic', lambda kind, **fields: rows.append({'kind': kind, **fields}))
    assert launchers._load_script_maker() is None
    assert len(rows) == 2 and all(r['exception_class'] == 'ImportError' for r in rows)
    assert 'DO_NOT_RETAIN' not in str(rows)


def test_missing_publication_python_refusal(tmp_path, monkeypatch):
    from hermes_cli import _launchers as launchers
    rows = []
    monkeypatch.setattr(launchers, 'resolve_store_python', lambda *args, **kwargs: None)
    monkeypatch.setattr(launchers, '_diagnostic', lambda kind, **fields: rows.append({'kind': kind, **fields}))
    assert launchers.stage_launcher('hermes', tmp_path, tmp_path / 'bin') is None
    assert rows == [{'kind': 'mint-refusal', 'reason': 'missing-store-python', 'filename': tmp_path / 'bin'}]


def test_observer_write_failure_never_throws(tmp_path):
    observer = load()
    (tmp_path / 'launcher-diagnostic').write_text('blocked')
    observer.snapshot(tmp_path, tmp_path, 'after', enabled=True)
