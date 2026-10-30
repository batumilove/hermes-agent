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
    payload = Path(__file__).with_name('fixtures') / 'distlib-0.4.2.tar.gz'
    bad = tmp_path / 'bad.tar.gz'
    bad.write_bytes(payload.read_bytes() + b'tampered')
    with pytest.raises(ValueError, match='fixture archive digest'):
        unpack_supplier(tmp_path / 'out', archive=bad)


def test_declared_supplier_ignores_external_override(native_supplier, monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_DISTLIB_SUPPLIER', str(tmp_path / 'absent'))
    from tests.native_supplier import unpack_supplier
    selected = unpack_supplier(tmp_path / 'independent')
    assert (selected / '__init__.py').read_bytes() == (native_supplier / '__init__.py').read_bytes()
