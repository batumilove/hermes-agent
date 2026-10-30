"""Hash-checked, pinned, repo-owned package fixture; no ambient imports/network.

Members are explicit JSON/base64 data, not an opaque renamed profile archive.
Every decoded member is authenticated before any file is written. Original
supplier hashes, provenance and license bytes remain in the sibling manifest.
"""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys


def unpack_supplier(destination, *, members=None):
    fixtures = Path(__file__).with_name('fixtures')
    manifest = json.loads((fixtures / 'distlib-0.4.2.json').read_text())
    member_root = Path(members or fixtures / 'distlib-0.4.2.members')
    records = [json.loads(path.read_text()) for path in sorted(member_root.glob('*.json'))]
    if any(set(record) != {'name', 'base64'} for record in records):
        raise ValueError('fixture member schema mismatch')
    payload = {record['name']: record['base64'] for record in records}
    if len(records) != len(payload) or set(payload) != set(manifest['files']):
        raise ValueError('fixture member inventory mismatch')
    from pm.python_bootstrap import DISTLIB_VERSION, _checked_distlib
    if manifest['version'] != DISTLIB_VERSION or DISTLIB_VERSION != '0.4.2':
        raise ValueError('fixture pin mismatch')
    decoded = {}
    for name, encoded in payload.items():
        if '/' in name or '\\' in name or name in ('.', '..'):
            raise ValueError('invalid fixture member')
        data = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(data).hexdigest() != manifest['files'][name]:
            raise ValueError('fixture member digest mismatch')
        decoded[name] = data
    supplier = Path(destination) / 'distlib'
    supplier.mkdir(parents=True)
    for name, data in decoded.items():
        (supplier / name).write_bytes(data)
    _checked_distlib(supplier, Path(destination))
    return supplier


def provision_windows_python(entry):
    """Private interpreter copy plus the exact selected standalone supplier.

    On Windows a base interpreter also needs its DLLs and standard library.
    Keep those bytes beside the copied executable; never mutate host Python.
    Linux callers use this only for inert fixture/PE-generation contracts.
    """
    entry = Path(entry)
    entry.mkdir(parents=True)
    base = Path(sys._base_executable).resolve()
    python = entry / 'python.exe'
    shutil.copy2(base, python)
    if sys.platform == 'win32':
        for dll in base.parent.glob('*.dll'):
            shutil.copy2(dll, entry / dll.name)
        for library in base.parent.glob('python*.zip'):
            shutil.copy2(library, entry / library.name)
        lib = base.parent / 'Lib'
        if lib.is_dir():
            shutil.copytree(lib, entry / 'Lib', ignore=shutil.ignore_patterns('site-packages', '__pycache__'))
        dlls = base.parent / 'DLLs'
        if dlls.is_dir():
            shutil.copytree(dlls, entry / 'DLLs')
    unpack_supplier(entry / 'Lib/site-packages')
    return python


def windows_unit_maker(python):
    """Real selected loader and PE writer, Windows selection injected, PE not run."""
    from hermes_cli import _launchers
    maker = _launchers._store_script_maker(python)
    assert maker is not None
    supplier = python.parent / 'Lib/site-packages/distlib'
    class WindowsUnitMaker(maker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._is_nt = True
        def _get_launcher(self, kind):
            return (supplier / 't64.exe').read_bytes()
    return WindowsUnitMaker
