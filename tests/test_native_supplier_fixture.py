"""Declared offline package fixture contracts (never execute a PE)."""
from pathlib import Path
import pytest


def test_declared_supplier_pin_and_resources(native_supplier):
    from pm.python_bootstrap import DISTLIB_VERSION, _checked_distlib, _distlib_version
    assert DISTLIB_VERSION == '0.4.2'
    _checked_distlib(native_supplier, native_supplier.parent)
    assert _distlib_version(native_supplier) == '0.4.2'
    for name in ('t32.exe', 't64.exe', 't64-arm.exe'):
        data = (native_supplier / name).read_bytes()
        assert data[:2] == b'MZ'
        offset = int.from_bytes(data[0x3c:0x40], 'little')
        assert data[offset:offset + 4] == b'PE\0\0'


def test_declared_supplier_rejects_tampering(tmp_path):
    from tests.native_supplier import unpack_supplier
    import json
    import shutil
    source = Path(__file__).with_name('fixtures') / 'distlib-0.4.2.members'
    bad = tmp_path / 'bad-members'
    shutil.copytree(source, bad)
    record = json.loads((bad / 'scripts.py.json').read_text())
    record['base64'] = json.loads((bad / 'LICENSE.txt.json').read_text())['base64']
    (bad / 'scripts.py.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='fixture member digest'):
        unpack_supplier(tmp_path / 'out', members=bad)


def test_declared_supplier_ignores_external_override(native_supplier, monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_DISTLIB_SUPPLIER', str(tmp_path / 'absent'))
    from tests.native_supplier import unpack_supplier
    selected = unpack_supplier(tmp_path / 'independent')
    assert (selected / '__init__.py').read_bytes() == (native_supplier / '__init__.py').read_bytes()
