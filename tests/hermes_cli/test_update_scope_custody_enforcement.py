"""Admission controls exercise native lifecycle and stash entry points."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from tests.hermes_cli.test_update_target_identity import git, update_tree


@pytest.fixture
def conflict_update_tree(request):
    """Alias so the test's parameter does not shadow the fixture-registration import (F811)."""
    return request.getfixturevalue("update_tree")


def test_profile_before_gateway_unit_is_discovered():
    from hermes_cli.update_cmd_fleet import _for_each_systemd_gateway_unit
    seen = []
    _for_each_systemd_gateway_unit(
        'hermes-pa-gateway.service loaded active running Private assistant\n',
        process_unit=seen.append, on_unit_timeout=lambda *_: pytest.fail('timeout'))
    assert seen == ['hermes-pa-gateway']


def test_real_conflict_index_and_worktree_survive_native_update(conflict_update_tree):
    from hermes_cli import main
    t = conflict_update_tree
    git(t.clone, 'checkout', '-q', 'main')
    git(t.clone, 'checkout', '-qb', 'conflicting')
    (t.clone / 'content.txt').write_text('local conflict\n')
    git(t.clone, '-c', 'commit.gpgsign=false', 'commit', '-qam', 'conflicting edit')
    git(t.clone, 'checkout', '-q', 'main')
    git(t.clone, 'fetch', 'origin', 'main')
    git(t.clone, 'merge', '--ff-only', 'origin/main')
    result = subprocess.run(['git', 'merge', 'conflicting'], cwd=t.clone, capture_output=True, check=False)
    assert result.returncode != 0
    before_index = (t.clone / '.git' / 'index').read_bytes()
    before_file = (t.clone / 'content.txt').read_bytes()
    before_head = git(t.clone, 'rev-parse', 'HEAD')
    from hermes_cli.update_cmd_stash import _stash_local_changes_if_needed
    with pytest.raises(subprocess.CalledProcessError):
        _stash_local_changes_if_needed(['git'], t.clone)
    assert (t.clone / '.git' / 'index').read_bytes() == before_index
    assert (t.clone / 'content.txt').read_bytes() == before_file
    t.args.channel = 'main'
    with pytest.raises(SystemExit) as error:
        main.cmd_update(t.args)
    assert error.value.code == 1
    assert (t.clone / '.git' / 'index').read_bytes() == before_index
    assert (t.clone / 'content.txt').read_bytes() == before_file
    assert git(t.clone, 'rev-parse', 'HEAD') == before_head
    assert not git(t.clone, 'stash', 'list')
    assert not t.requests


def test_unreadable_user_unit_is_not_assumed_default(monkeypatch):
    from hermes_cli import update_cmd_fleet as fleet
    from hermes_cli.update_fleet_scope import systemd_unit_in_update_scope
    monkeypatch.setattr(fleet, '_unit_main_pid', lambda *_: 0)
    monkeypatch.setattr(fleet, '_systemctl', lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=''))
    assert systemd_unit_in_update_scope(['--user'], 'hermes-gateway', {Path.home() / '.hermes'}) is None


def test_pidfile_hint_cannot_override_foreign_process_home(monkeypatch):
    from hermes_cli import update_cmd_fleet as fleet
    from hermes_cli import update_fleet_scope as scope
    monkeypatch.setattr(scope, 'gateway_pid_home', lambda pid: '/foreign/hermes')
    monkeypatch.setattr(scope, 'update_scope_homes', lambda: {Path('/owned/hermes')})
    assert fleet._scoped_manual_gateway_pids([1234], keep=[1234], quiet=True) == []


def test_unmerged_index_refuses_before_reset_or_stash(tmp_path, monkeypatch):
    from hermes_cli import update_cmd_stash as stash
    from hermes_cli import update_cmd_git as git
    calls = []
    def run(_git, args, *_a, **_kw):
        calls.append(args)
        output = 'UU conflict.py\0' if args[0] == 'status' else '100644 abc 1\tconflict.py\n' if args[0] == 'ls-files' else ''
        return SimpleNamespace(returncode=0, stdout=output, stderr='')
    monkeypatch.setattr(git, '_git_run', run)
    with pytest.raises(subprocess.CalledProcessError):
        stash._stash_local_changes_if_needed(['git'], tmp_path)
    assert not any(args[0] in {'reset', 'stash', 'add'} for args in calls)
