"""Pinned supplier delivery and selected-interpreter fixture contracts."""
import json
from pathlib import Path
import shutil

import pytest

from tests import native_supplier


def test_supplier_is_explicit_member_data_not_profile_archive():
    fixtures = Path(native_supplier.__file__).with_name('fixtures')
    assert not (fixtures / 'distlib-0.4.2.tar.gz').exists()
    records = [json.loads(path.read_text()) for path in (fixtures / 'distlib-0.4.2.members').glob('*.json')]
    manifest = json.loads((fixtures / 'distlib-0.4.2.json').read_text())
    assert {record['name'] for record in records} == set(manifest['files'])
    assert len(records) == len(manifest['files'])
    assert 'LICENSE.txt' in {record['name'] for record in records}


def test_selected_windows_interpreter_fixture_has_own_pinned_supplier(tmp_path):
    python = native_supplier.provision_windows_python(tmp_path / 'python')
    supplier = python.parent / 'Lib/site-packages/distlib'
    from pm.python_bootstrap import _checked_distlib
    _checked_distlib(supplier, python.parent)
    assert python.is_file()


def test_supplier_rejects_member_tamper_before_writing(tmp_path):
    source = Path(__file__).with_name('fixtures') / 'distlib-0.4.2.members'
    bad = tmp_path / 'bad-members'
    shutil.copytree(source, bad)
    record = json.loads((bad / 'scripts.py.json').read_text())
    record['base64'] = json.loads((bad / 'LICENSE.txt.json').read_text())['base64']
    (bad / 'scripts.py.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='fixture member digest'):
        native_supplier.unpack_supplier(tmp_path / 'out', members=bad)
    assert not (tmp_path / 'out').exists()
