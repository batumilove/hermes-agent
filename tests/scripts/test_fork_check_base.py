"""Actual Git ancestry determines whether an upstream import resets the comparison tree."""
import subprocess

import pytest

from scripts.fork_check_base import accepted_upstream, comparison_base


def _git(repo, *args):
    return subprocess.run(
        ['git', *args], cwd=repo, check=True, capture_output=True, text=True,
        timeout=30,
    ).stdout.strip()


@pytest.fixture
def graph(tmp_path):
    _git(tmp_path, 'init', '-b', 'main')
    _git(tmp_path, 'config', 'user.name', 'Test')
    _git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    _git(tmp_path, 'commit', '--allow-empty', '-m', 'base')
    base = _git(tmp_path, 'rev-parse', 'HEAD')
    _git(tmp_path, 'checkout', '-b', 'upstream')
    _git(tmp_path, 'commit', '--allow-empty', '-m', 'upstream')
    accepted = _git(tmp_path, 'rev-parse', 'HEAD')
    _git(tmp_path, 'checkout', 'main')
    _git(tmp_path, 'commit', '--allow-empty', '-m', 'fork')
    fork = _git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / '.github').mkdir()
    (tmp_path / '.github/upstream-base').write_text(accepted + '\n', encoding='utf-8')
    return tmp_path, base, fork, accepted


def test_import_checks_ports_against_upstream_then_locks_in_improvements(graph):
    repo, _, fork, accepted = graph
    _git(repo, 'merge', '--no-ff', 'upstream', '-m', 'reconcile')
    merged = _git(repo, 'rev-parse', 'HEAD')
    assert comparison_base(repo, fork) == accepted
    _git(repo, 'commit', '--allow-empty', '-m', 'later fix')
    assert comparison_base(repo, merged) == merged


def test_recorded_commit_must_be_an_ancestor(graph):
    repo, _, _, _ = graph
    with pytest.raises(ValueError, match='not an ancestor'):
        accepted_upstream(repo)


def test_normal_repo_preserves_comparison_and_malformed_record_fails(graph):
    repo, base, _, _ = graph
    path = repo / '.github/upstream-base'
    path.unlink()
    assert comparison_base(repo, base) == base
    path.write_text('HEAD\n', encoding='utf-8')
    with pytest.raises(ValueError, match='full SHA'):
        accepted_upstream(repo)
