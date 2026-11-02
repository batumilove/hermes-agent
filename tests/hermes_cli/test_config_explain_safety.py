"""Fresh-process regressions for the strictly read-only command."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEY = 'approvals.destructive_slash_confirm'


def snapshot(root):
    return {str(p.relative_to(root)): ('dir' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if '.git' not in p.parts}


def run(tmp_path, mode='module', raw=None, flags=(), sticky=None, env_home=True):
    home = tmp_path / '.hermes'
    home.mkdir(exist_ok=True)
    managed = tmp_path / 'managed'
    managed.mkdir(exist_ok=True)
    if raw is not None:
        (home / 'config.yaml').write_text(raw)
    if sticky:
        named = home / 'profiles' / sticky
        named.mkdir(parents=True, exist_ok=True)
        (named / 'config.yaml').write_text('approvals: {destructive_slash_confirm: false}\n')
        (home / 'active_profile').write_text(sticky)
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path),
           'HERMES_MANAGED_DIR': str(managed), 'PYTHONDONTWRITEBYTECODE': '1'}
    if env_home:
        env['HERMES_HOME'] = str(home)
    args = [*flags, 'config', 'explain', KEY, '--json']
    if mode == 'direct':
        cmd = [sys.executable, '-B', '-c',
               'import json,sys; from hermes_cli.config_explain import explain_config; '
               f'print(json.dumps(explain_config({KEY!r}))); '
               'assert "hermes_cli.config" not in sys.modules; '
               'assert "hermes_cli.providers" not in sys.modules; '
               'assert "hermes_bootstrap" not in sys.modules']
    elif mode == 'console':
        cmd = [sys.executable, '-B', '-c', 'from hermes_cli.main import main; main()', *args]
    else:
        cmd = [sys.executable, '-B', '-m', 'hermes_cli.main', *args]
    before, source_before = snapshot(tmp_path), snapshot(ROOT)
    proc = subprocess.run(cmd, check=False, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    assert snapshot(tmp_path) == before
    assert snapshot(ROOT) == source_before
    assert proc.stderr == '', proc.stderr
    assert 'UNRELATED_PRIVATE_SENTINEL' not in proc.stdout + proc.stderr
    return proc, json.loads(proc.stdout)


@pytest.mark.parametrize('mode', ['direct', 'module', 'console'])
@pytest.mark.parametrize('raw', [None, 'approvals: {destructive_slash_confirm: false}\nunrelated: ${vault:UNRELATED_PRIVATE_SENTINEL}\n'])
def test_readonly_no_discovery_or_unrelated_expansion(tmp_path, mode, raw):
    proc, data = run(tmp_path, mode, raw)
    assert proc.returncode == 0
    assert data['value'] is (raw is None)
    assert data['profile'] == 'default'


@pytest.mark.parametrize('leaf', ['null', '0', '1', '[]', '{}', 'wrong', "'${vault:UNRELATED_PRIVATE_SENTINEL}'"])
def test_malformed_leaf_is_blocked_without_text_leak(tmp_path, leaf):
    proc, data = run(tmp_path, raw=f'approvals: {{destructive_slash_confirm: {leaf}}}\n')
    assert proc.returncode == 2
    assert data['status'] == 'blocked'
    assert 'value' not in data


@pytest.mark.parametrize('flags,sticky,profile,basis,value', [
    ((), None, 'default', 'environment', True),
    ((), 'work', 'work', 'sticky', False),
    (('-p', 'default'), 'work', 'default', 'explicit', True),
    (('--profile=WORK',), 'work', 'work', 'explicit', False),
])
def test_profile_selection(tmp_path, flags, sticky, profile, basis, value):
    proc, data = run(tmp_path, flags=flags, sticky=sticky)
    assert proc.returncode == 0
    assert data['profile'] == profile
    assert data['profile_selection'] == basis
    assert data['value'] is value


@pytest.mark.parametrize('managed_raw,expected', [
    ('approvals: {destructive_slash_confirm: true}\nunrelated: ${vault:UNRELATED_PRIVATE_SENTINEL}\n', True),
    ('approvals: {destructive_slash_confirm: null}\n', None),
    ('approvals: [', None),
])
def test_managed_readonly_and_strict_failure(tmp_path, managed_raw, expected):
    managed = tmp_path / 'managed'
    managed.mkdir()
    (managed / 'config.yaml').write_text(managed_raw)
    proc, data = run(tmp_path, raw='approvals: {destructive_slash_confirm: false}\n')
    if expected is None:
        assert proc.returncode == 2 and data['status'] == 'blocked'
    else:
        assert proc.returncode == 0 and data['value'] is expected and data['origin'] == 'managed'


def test_platform_default_identity(tmp_path):
    proc, data = run(tmp_path, env_home=False)
    assert proc.returncode == 0
    assert data['profile'] == 'default'
    assert data['profile_selection'] == 'default'


def test_classifier_does_not_treat_flag_value_as_command():
    from hermes_cli.config_explain_entry import is_explain_command
    assert is_explain_command(['config', 'explain', KEY, '--json'])
    assert not is_explain_command(['--model', 'config', 'explain', KEY])
    assert not is_explain_command(['chat', '-q', 'config', 'explain', KEY])
    assert not is_explain_command(['--provider', 'config', 'explain', KEY])


def test_missing_managed_scope_and_custom_home_identity(tmp_path):
    home = tmp_path / 'custom-home'
    home.mkdir()
    (home / '.env').write_text('HERMES_HOME=/must-not-be-loaded\n')
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'HERMES_HOME': str(home),
           'HERMES_MANAGED_DIR': str(tmp_path / 'missing'), 'PYTHONDONTWRITEBYTECODE': '1'}
    before = snapshot(tmp_path)
    proc = subprocess.run([sys.executable, '-B', '-m', 'hermes_cli.main', 'config', 'explain', KEY, '--json'],
                          check=False, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and proc.stderr == ''
    data = json.loads(proc.stdout)
    assert data['home'] == str(home)
    # The canonical home primitive, not a basename heuristic, owns the identity.
    assert data['profile'] == 'default'
    assert data['managed_key_present'] is False
    assert snapshot(tmp_path) == before
