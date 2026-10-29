"""Unchanged external manifest through real runtime reader and plugin discovery."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import upstream_compatibility as compat
from tests.plugins.external.langfuse_fixture import PLUGIN_DIR

pytest_plugins = ['tests.hermes_cli.test_upstream_release_custody']


@pytest.mark.parametrize('mode', ['first', 'replacement'])
def test_continuous_producer_to_fresh_langfuse(release_repo, mode):
    import subprocess
    import sys

    from tests.hermes_cli.test_upstream_producer_lifecycle import capture
    from tests.hermes_cli.test_upstream_release_custody import git

    repo, old = release_repo
    if mode == 'first':
        git(repo, 'rm', compat.RECEIPT_PATH)
        git(repo, 'commit', '-qm', 'no custody')
    else:
        (repo / compat.PROVENANCE_PATH).write_text(json.dumps(dict(old['proof'], fetch_depth=9)))
        git(repo, 'add', compat.PROVENANCE_PATH)
        git(repo, 'commit', '-qm', 'new proof')
    git(repo, 'tag', '-a', 'v0.14.0', '-m', 'fork')
    git(repo, 'tag', '-d', old['release_tag'])
    home = repo / 'profile'
    home.mkdir()
    shutil.copytree(PLUGIN_DIR, home / 'plugins/langfuse', ignore=shutil.ignore_patterns('__pycache__'))
    (home / 'config.yaml').write_text('plugins:\n  enabled: [langfuse]\n')
    anchor = {k: old[k] for k in compat.REVIEWED_RELEASES[0]}
    program = r'''import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from hermes_cli import upstream_compatibility as c, plugins, version_info
from pm import paths
repo = Path(sys.argv[1])
expected = sys.argv[3] == 'yes'
c.REVIEWED_RELEASES = (json.loads(sys.argv[2]),)
c.__file__ = str(repo / 'hermes_cli/upstream_compatibility.py')
version_info.__file__ = str(repo / 'hermes_cli/version_info.py')
paths.__file__ = str(repo / 'pm/paths.py')
version_info._cached_version_info = None
os.environ['HERMES_HOME'] = str(repo / 'profile')
Path.home = lambda: repo / 'profile'
identity = version_info.get_version_info()
assert identity.base_version == '0.14.0'
assert identity.source == ('docker' if sys.argv[4] == 'stamp' else 'git')
assert c.verified_upstream_version() == (sys.argv[5] if expected else None)
manager = plugins.PluginManager()
manager.discover_and_load()
loaded = manager._plugins['langfuse']
assert loaded.enabled is expected, loaded.error
if expected:
    assert loaded.module is not None
    assert any(r.plugin_key == 'langfuse' for r in manager._registration_order)
    fake = SimpleNamespace()
    loaded.module._LANGFUSE_CLIENT = fake
    assert loaded.module._get_langfuse() is fake
else:
    assert loaded.module is None
    assert 'requires' in loaded.error.lower()
assert version_info.get_version_info() == identity
'''
    def fresh(accepted, fault='none'):
        subprocess.run([sys.executable, '-c', program, str(repo), json.dumps(anchor),
                        'yes' if accepted else 'no', fault, old['release_tag'][1:]], check=True)

    proposed = compat.produce_receipt(repo, capture(old))
    fresh(False)
    receipt_path = repo / compat.RECEIPT_PATH
    exact = json.dumps(proposed).encode('utf-8')
    receipt_path.write_bytes(exact)
    fresh(False)
    git(repo, 'add', compat.RECEIPT_PATH)
    git(repo, 'commit', '-qm', 'commit producer bytes')
    assert subprocess.check_output(['git', 'show', f'HEAD:{compat.RECEIPT_PATH}'], cwd=repo) == exact
    fresh(True)
    receipt_path.unlink()
    fresh(False, 'missing')
    receipt_path.write_bytes(exact + b'\n')
    fresh(False, 'stale')
    receipt_path.write_bytes(exact)
    (repo / 'install-stamp.json').write_text(json.dumps(dict(commit='a' * 40, baseVersion='0.14.0',
        displayVersion='0.14.0', updateMechanism='external', source='docker')))
    fresh(False, 'stamp')


@pytest.mark.parametrize('custody', ['valid', 'missing', 'stale', 'stamp'])
def test_receipt_to_real_langfuse_registration(release_repo, monkeypatch, custody):
    from hermes_cli import plugins, version_info
    from pm import paths

    repo, receipt = release_repo
    home = repo / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setattr(compat, '__file__', str(repo / 'hermes_cli/upstream_compatibility.py'))
    monkeypatch.setattr(version_info, '__file__', str(repo / 'hermes_cli/version_info.py'))
    monkeypatch.setattr(paths, '__file__', str(repo / 'pm/paths.py'))
    monkeypatch.setattr(version_info, '_cached_version_info', None)
    # Real canonical Git reader sees an old fork tag; no version-return stubs.
    from tests.hermes_cli.test_upstream_release_custody import git
    git(repo, 'tag', '-a', 'v0.14.0', '-m', 'fork')
    git(repo, 'tag', '-d', receipt['release_tag'])
    if custody == 'missing':
        (repo / compat.RECEIPT_PATH).unlink()
    elif custody == 'stale':
        p = repo / compat.RECEIPT_PATH
        p.write_bytes(p.read_bytes() + b'\n')
    elif custody == 'stamp':
        (repo / 'install-stamp.json').write_text(json.dumps(dict(commit='a' * 40, baseVersion='0.14.0', displayVersion='0.14.0', updateMechanism='docker', source='docker')))
        assert version_info._resolve_stamp_file() == repo / 'install-stamp.json'
        assert version_info.get_version_info().base_version == '0.14.0'
        assert compat.verified_upstream_version() is None
    shutil.copytree(PLUGIN_DIR, home / 'plugins/langfuse', ignore=shutil.ignore_patterns('__pycache__'))
    (home / 'config.yaml').write_text('plugins:\n  enabled: [langfuse]\n')
    manager = plugins.PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins['langfuse']
    if custody != 'valid':
        assert not loaded.enabled
        assert loaded.module is None
        assert 'requires' in loaded.error.lower()
    else:
        assert loaded.enabled, loaded.error
        assert loaded.module is not None
        assert any(r.plugin_key == 'langfuse' for r in manager._registration_order)
        # Client transport is fake; registration and manifest remain real.
        fake = SimpleNamespace()
        monkeypatch.setattr(loaded.module, '_LANGFUSE_CLIENT', fake)
        assert loaded.module._get_langfuse() is fake
