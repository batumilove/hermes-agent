"""Fresh-process regressions for the strictly read-only command."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEY = 'approvals.destructive_slash_confirm'


@pytest.fixture(scope='module')
def source_tree(tmp_path_factory):
    # Copy every tracked input, not just today's import closure. Other canonical
    # test-file workers may write caches in ROOT, but never in this private tree.
    source = tmp_path_factory.mktemp('explain-source')
    tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True,
                             capture_output=True).stdout.decode().split('\0')
    for name in filter(None, tracked):
        original, copied = ROOT / name, source / name
        copied.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            copied.symlink_to(os.readlink(original))
        else:
            shutil.copyfile(original, copied)
    return source


@pytest.fixture(autouse=True)
def isolated_source(source_tree, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', source_tree)


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


@pytest.mark.parametrize('write_path', ['.pytest_cache/v/cache/nodeids',
                                        'hermes_cli/__pycache__/probe.pyc',
                                        'unexpected.txt'])
def test_source_snapshot_detects_all_child_writes(tmp_path, monkeypatch, write_path):
    original_run = subprocess.run

    def writing_child(*args, **kwargs):
        proc = original_run(*args, **kwargs)
        assert proc.returncode == 0
        original_run([sys.executable, '-B', '-c',
                      'from pathlib import Path; import sys; p = Path(sys.argv[1]); '
                      'p.parent.mkdir(parents=True, exist_ok=True); '
                      'p.write_bytes(b"child write must be detected")', write_path],
                     cwd=kwargs['cwd'], env=kwargs['env'], check=True)
        return proc

    monkeypatch.setattr(subprocess, 'run', writing_child)
    with pytest.raises(AssertionError):
        run(tmp_path, mode='direct')


def test_source_probe_ignores_unrelated_checkout_writer(tmp_path, source_tree, monkeypatch):
    shared = tmp_path / 'runner-checkout'
    shared.mkdir()
    home = tmp_path / 'probe-home'
    home.mkdir()
    original_run = subprocess.run

    def concurrent_writer(*args, **kwargs):
        assert Path(kwargs['cwd']) == source_tree
        proc = original_run(*args, **kwargs)
        (shared / 'nodeids').write_bytes(b'unrelated pytest cache')
        return proc

    monkeypatch.setattr(subprocess, 'run', concurrent_writer)
    proc, _data = run(home, mode='direct')
    assert proc.returncode == 0
    assert (shared / 'nodeids').read_bytes() == b'unrelated pytest cache'


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


@pytest.mark.parametrize('error_type', [OSError, RuntimeError, ImportError])
def test_profile_resolution_failure_keeps_startup_without_exception_text(tmp_path, monkeypatch, capsys, error_type):
    from hermes_cli import profile_selection, profiles

    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(sys, 'argv', ['hermes', '-p', 'work', 'chat'])

    def fail(_name):
        raise error_type('UNRELATED_PRIVATE_SENTINEL')

    monkeypatch.setattr(profiles, 'resolve_profile_env', fail)
    profile_selection._apply_profile_override()
    assert os.environ['HERMES_HOME'] == str(home)
    assert sys.argv == ['hermes', '-p', 'work', 'chat']
    assert profile_selection.explicit_cli_profile() is None
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err == 'Warning: profile override failed, using default\n'


def test_platform_default_identity(tmp_path):
    proc, data = run(tmp_path, env_home=False)
    assert proc.returncode == 0
    assert data['profile'] == 'default'
    assert data['profile_selection'] == 'default'


@pytest.mark.parametrize('flags', [
    ('--model', 'foo'), ('--model=foo',), ('-m', 'foo'), ('-mfoo',),
    ('--provider', 'custom'), ('--provider=custom',),
    ('--pr=custom',), ('--pro=custom',),
    ('--pr=custom', '--profile', 'default'),
    ('--profile=default', '--pro=custom'),
    ('--model', 'foo', '--profile', 'default'),
    ('--profile', 'default', '--provider', 'custom'),
    ('-p', 'default', '-mfoo'), ('--profile=default', '--model=foo'),
    ('--',), ('--reasoning', 'high'), ('-w', '-mfoo'),
    ('--continue=session',), ('--continue', '--'),
    pytest.param(('--cpu-profile', 'trace'), id='cpu_profile_separate'),
    pytest.param(('--cp=trace',), id='cpu_profile_abbreviated_equals'),
])
def test_parser_supported_globals_remain_filesystem_readonly(tmp_path, flags):
    from hermes_cli._parser import build_top_level_parser
    from hermes_cli.subcommands.config import build_config_parser

    clean = list(flags)
    for flag in ('--profile', '-p'):
        if flag in clean:
            index = clean.index(flag)
            del clean[index:index + 2]
    clean = [arg for arg in clean if not arg.startswith('--profile=')]
    parser, subs, _ = build_top_level_parser()
    build_config_parser(subs, cmd_config=lambda args: None)
    args = parser.parse_args([*clean, 'config', 'explain', KEY, '--json'])
    assert (args.command, args.config_command, args.key) == ('config', 'explain', KEY)
    proc, data = run(tmp_path, flags=flags)
    assert proc.returncode == 0
    assert data['status'] == 'ok' and data['value'] is True


def test_gate_matches_systematic_actual_parser_prefixes():
    import contextlib
    import io
    from hermes_cli import _config_explain_argv
    from hermes_cli._parser import build_top_level_parser
    from hermes_cli.subcommands.config import build_config_parser

    parser, subs, _ = build_top_level_parser()
    build_config_parser(subs, cmd_config=lambda args: None)
    tail = ['config', 'explain', KEY, '--json']
    prefixes = [[], ['--'], ['-wmfoo'], ['-wsone'], ['-csession'],
                ['-w', '--continue', '--'], ['--skills=a', '-sb'],
                ['--model='], ['--model', '-2']]
    for action in parser._actions:
        for flag in action.option_strings:
            if flag.startswith('--') and action.dest not in {'help', 'version'}:
                for n in range(3, len(flag) + 1):
                    prefix = flag[:n]
                    prefixes.append([prefix] if action.nargs == 0 else [prefix + '=sample'])
    accepted, rejected, mismatches = [], [], []
    for prefix in prefixes:
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                args = parser.parse_args(prefix + tail)
        except SystemExit:
            rejected.append(prefix)
            assert _config_explain_argv(prefix + tail) is None, prefix
            continue
        assert (args.command, args.config_command) == ('config', 'explain')
        accepted.append(prefix)
        if _config_explain_argv(prefix + tail) != tail:
            mismatches.append(prefix)
    print(json.dumps({'accepted': len(accepted), 'rejected': len(rejected),
                      'mismatches': mismatches, 'accepted_prefixes': accepted}))
    assert not mismatches


def test_gate_matches_every_parser_global_option():
    from hermes_cli import _config_explain_argv
    from hermes_cli._parser import build_top_level_parser
    from hermes_cli.subcommands.config import build_config_parser

    parser, subs, _ = build_top_level_parser()
    build_config_parser(subs, cmd_config=lambda args: None)
    tail = ['config', 'explain', KEY, '--json']
    for action in parser._actions:
        if action.dest in {'help', 'version'}:
            continue
        for flag in action.option_strings:
            variants = [[flag]] if action.nargs == 0 else [[flag, 'sample'], [flag + '=sample']]
            if action.nargs == '?':
                variants.append([flag, '--'])
            for prefix in variants:
                args = parser.parse_args(prefix + tail)
                assert (args.command, args.config_command) == ('config', 'explain')
                assert _config_explain_argv(prefix + tail) == tail, prefix


@pytest.mark.parametrize('argv', [
    ['--unknown', 'config', 'explain'], ['--model'],
    ['--model', '--provider', 'config', 'explain'],
    ['--continue', 'config', 'explain'],
    ['--help', 'config', 'explain'], ['--version', 'config', 'explain'],
    ['chat', '--', 'config', 'explain'], ['config', 'set', 'config', 'explain'],
])
def test_gate_declines_unknown_malformed_or_other_commands(argv):
    from hermes_cli import _config_explain_argv
    assert _config_explain_argv(argv) is None


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
