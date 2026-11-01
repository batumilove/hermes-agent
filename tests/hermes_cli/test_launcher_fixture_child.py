"""Exercise real selected loading/publication in a fresh fixture child, not PE execution."""
import subprocess
import sys

from tests.hermes_cli.test_source_launcher_publication import fixture_tree
from tests.native_supplier import provision_windows_python


def test_fresh_fixture_process_loads_selected_supplier(tmp_path, monkeypatch):
    repo, _home, _python = fixture_tree(tmp_path, monkeypatch)
    selected = provision_windows_python(tmp_path / 'selected')
    code = (
        'import sys; sys.path.insert(0, sys.argv[1]); '
        'from pathlib import Path; from hermes_cli._launchers import _store_script_maker; '
        'maker = _store_script_maker(Path(sys.argv[2])); '
        'assert maker is not None; print(maker.__module__)'
    )
    child = subprocess.run([sys.executable, '-I', '-c', code, str(repo), str(selected)],
                           capture_output=True, text=True, timeout=30, check=False)
    assert child.returncode == 0, child.stdout + child.stderr
    assert '_hermes_store_distlib_' in child.stdout


def test_fresh_fixture_republication_has_complete_loader_closure(tmp_path, monkeypatch):
    repo, _home, _python = fixture_tree(tmp_path, monkeypatch)
    selected = provision_windows_python(tmp_path / 'selected')
    # Deliberately generate actual PE bytes without executing them. Only the
    # Windows writer selection/resource edge is adapted on this POSIX child.
    code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_cli import _launchers as subject
root, python = Path(sys.argv[1]), Path(sys.argv[2])
subject._is_windows = lambda: True
subject.resolve_store_python = lambda *args, **kwargs: python
load = subject._store_script_maker
def windows_maker(python):
    maker = load(python)
    if maker is None:
        return None
    class WindowsMaker(maker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._is_nt = True
        def _get_launcher(self, kind):
            return (python.parent / 'Lib/site-packages/distlib/t64.exe').read_bytes()
    return WindowsMaker
subject._store_script_maker = windows_maker
out = root / '.hermes/bin'
for attempt in range(2):
    written = subject.ensure_install_launchers(root, out)
    print('published', len(written), len(subject.ENTRY_POINTS), flush=True)
    assert len(written) == len(subject.ENTRY_POINTS)
'''
    child = subprocess.run([sys.executable, '-I', '-c', code, str(repo), str(selected)],
                           capture_output=True, text=True, timeout=30, check=False)
    assert child.returncode == 0, child.stdout + child.stderr
    assert child.stdout.count('published 2 2') == 2
