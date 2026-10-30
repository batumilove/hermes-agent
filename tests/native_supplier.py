"""Hash-checked, pinned, repo-owned package fixture; no ambient imports/network."""
import hashlib
import json
from pathlib import Path
import tarfile


def unpack_supplier(destination, *, archive=None):
    fixtures = Path(__file__).with_name('fixtures')
    manifest = json.loads((fixtures / 'distlib-0.4.2.json').read_text())
    archive = archive or fixtures / 'distlib-0.4.2.tar.gz'
    if hashlib.sha256(Path(archive).read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError('fixture archive digest mismatch')
    destination = Path(destination)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if {m.name for m in members} != {'distlib/' + n for n in manifest['files']} or len(members) != len(manifest['files']):
            raise ValueError('fixture member inventory mismatch')
        for member in members:
            name = member.name.removeprefix('distlib/')
            if not member.isfile() or '/' in name or name in ('.', '..'):
                raise ValueError('invalid fixture member')
            member_file = source.extractfile(member)
            if member_file is None:
                raise ValueError('unreadable fixture member')
            data = member_file.read()
            if hashlib.sha256(data).hexdigest() != manifest['files'][name]:
                raise ValueError('fixture member digest mismatch')
            path = destination / 'distlib' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    supplier = destination / 'distlib'
    from pm.python_bootstrap import DISTLIB_VERSION, _checked_distlib
    if manifest['version'] != DISTLIB_VERSION or DISTLIB_VERSION != '0.4.2':
        raise ValueError('fixture pin mismatch')
    _checked_distlib(supplier, destination)
    return supplier


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
