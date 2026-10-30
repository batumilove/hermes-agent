"""Linux unit contracts; injected Windows sharing failures are NOT native proof."""
from pathlib import Path
import os
import shutil
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from hermes_cli import _launchers as subject


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    python = tmp_path / 'store' / 'python.exe'
    python.parent.mkdir()
    python.touch()
    supplier = python.parent / 'Lib/site-packages/distlib'
    shutil.copytree(Path(os.environ['TEST_DISTLIB_SUPPLIER']), supplier)
    out = tmp_path / 'bin'
    out.mkdir()
    monkeypatch.setattr(subject, '_is_windows', lambda: True)
    monkeypatch.setattr(subject, '_load_script_maker', lambda: (_ for _ in ()).throw(AssertionError('ambient forbidden')))
    monkeypatch.setattr(subject, 'resolve_store_python', lambda *a, **k: python)
    # Distlib normally selects PE generation only on Windows. This explicit
    # unit adapter uses its real PE resource/ZIP writer without executing PE.
    load = subject._store_script_maker
    def windows_maker(python):
        maker = load(python)
        if maker is None:
            return None
        class WindowsUnitMaker(maker):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._is_nt = True
            def _get_launcher(self, kind):
                return (supplier / 't64.exe').read_bytes()
        return WindowsUnitMaker
    monkeypatch.setattr(subject, '_store_script_maker', windows_maker)
    return tmp_path, python, supplier, out


def old_pair(out):
    for name in subject.WINDOWS_BIN_LAUNCHERS:
        (out / f'{name}.exe').write_bytes(('old-' + name).encode())


def assert_old(out):
    for name in subject.WINDOWS_BIN_LAUNCHERS:
        assert (out / f'{name}.exe').read_bytes() == ('old-' + name).encode()
    assert not list(out.glob('*.cmd'))


def test_explicit_pip_free_selected_supplier(runtime):
    root, python, supplier, out = runtime
    assert not (python.parent / 'Lib/site-packages/pip').exists()
    with patch.object(subject, '_load_script_maker', side_effect=AssertionError('ambient forbidden')):
        maker = subject._store_script_maker(python)
        assert maker is not None
        assert any(base.__module__.startswith('_hermes_store_distlib_') for base in maker.__mro__)


@pytest.mark.parametrize('resource', ['scripts.py', 'resources.py', 't64.exe', '__init__.py'])
def test_missing_supplier_resource_fails_closed(runtime, resource):
    root, python, supplier, out = runtime
    (supplier / resource).unlink()
    old_pair(out)
    assert subject.ensure_install_launchers(root, out) == []
    assert_old(out)


def test_both_names_native_from_selected_supplier(runtime):
    root, python, supplier, out = runtime
    paths = subject.ensure_install_launchers(root, out)
    assert {Path(p).name for p in paths} == {'hermes.exe', 'hermes-acp.exe'}
    for p in paths:
        assert Path(p).read_bytes().startswith(b'MZ')
        assert subject._launcher_python(Path(p)) == python
        with ZipFile(p) as archive:
            assert archive.read('__main__.py') == subject._launcher_script(Path(p).stem, root, None).encode()


@pytest.mark.parametrize('bad_name', ['hermes', 'hermes-acp'])
def test_invalid_maker_output_preserves_both(runtime, monkeypatch, bad_name):
    root, python, supplier, out = runtime
    old_pair(out)
    real = subject.mint_launcher
    def invalid(name, root, directory, python, site):
        if name == bad_name:
            result = directory / f'{name}.exe'
            result.write_bytes(b'invalid')
            return result
        return real(name, root, directory, python, site)
    monkeypatch.setattr(subject, 'mint_launcher', invalid)
    assert subject.ensure_install_launchers(root, out) == []
    assert_old(out)


@pytest.mark.parametrize('fault', ['publish', 'rename', 'rollback'])
def test_transaction_failure_preserves_recovery(runtime, monkeypatch, fault):
    root, python, supplier, out = runtime
    old_pair(out)
    replace = os.replace
    def injected(src, dst):
        src, dst = Path(src), Path(dst)
        if dst == out / 'hermes-acp.exe' and src.parent != out:
            raise PermissionError('injected publication failure')
        if fault == 'rename' and src == out / 'hermes.exe':
            raise PermissionError('injected locked rename')
        if fault == 'rollback' and dst == out / 'hermes.exe' and src.parent == out:
            raise PermissionError('injected rollback failure')
        return replace(src, dst)
    monkeypatch.setattr(subject.os, 'replace', injected)
    assert subject.ensure_install_launchers(root, out) == []
    if fault != 'rollback':
        assert_old(out)
    else:
        assert any(p.read_bytes() == b'old-hermes' for p in out.iterdir() if p.is_file())
        assert (out / 'hermes-acp.exe').read_bytes() == b'old-hermes-acp'


def test_exposure_failure_reaches_sync_receipt(runtime, monkeypatch):
    from hermes_cli import venv_sync
    import pm
    root, python, supplier, out = runtime
    (root / 'pyproject.toml').touch()
    monkeypatch.setattr(venv_sync, '_is_sealed', lambda root: False)
    monkeypatch.setattr(subject, 'ensure_install_launchers', lambda *a: ['hermes.exe', 'hermes-acp.exe'])
    monkeypatch.setattr(subject, 'expose_cli', lambda *a, **k: {'ok': False, 'error': 'native publication failed'})
    monkeypatch.setattr(pm, 'venv_is_current', lambda **k: True)
    result = venv_sync.sync(root)
    assert result['ok'] is False
    assert result['state'] == 'failed'
    assert 'native publication failed' in result['detail']


def test_locked_old_image_renamed_without_unlink(runtime, monkeypatch):
    root, python, supplier, out = runtime
    old_pair(out)
    unlink = Path.unlink
    rename = os.replace
    moved = []
    def locked_unlink(path, *a, **k):
        if path.parent == out and (path.suffix == '.exe' or path.suffix == '.rollback'):
            raise PermissionError('mapped image')
        return unlink(path, *a, **k)
    def witness(src, dst):
        if Path(src).parent == out and Path(src).suffix == '.exe':
            moved.append(Path(src).name)
        return rename(src, dst)
    monkeypatch.setattr(Path, 'unlink', locked_unlink)
    monkeypatch.setattr(subject.os, 'replace', witness)
    assert len(subject.ensure_install_launchers(root, out)) == 2
    assert moved == ['hermes.exe', 'hermes-acp.exe']
    assert len(list(out.glob('*.rollback'))) == 2
    assert {p.read_bytes() for p in out.glob('*.rollback')} == {b'old-hermes', b'old-hermes-acp'}


def test_invalid_selected_version_rejected(runtime):
    root, python, supplier, out = runtime
    (supplier / '__init__.py').write_text("__version__ = '0.0.0'\n", encoding='utf-8')
    old_pair(out)
    assert subject.ensure_install_launchers(root, out) == []
    assert_old(out)


def test_no_store_interpreter_sync_is_failure(runtime, monkeypatch):
    from hermes_cli import venv_sync
    import pm
    root, python, supplier, out = runtime
    (root / 'pyproject.toml').touch()
    monkeypatch.setattr(venv_sync, '_is_sealed', lambda root: False)
    monkeypatch.setattr(subject, 'resolve_store_python', lambda *a, **k: None)
    monkeypatch.setattr(pm, 'venv_is_current', lambda **k: True)
    assert venv_sync.sync(root)['ok'] is False
