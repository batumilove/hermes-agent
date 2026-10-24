"""Real Git ancestry contracts for fork contributor reporting."""
from pathlib import Path
import subprocess

import pytest

from scripts.fork_contributors import new_author_emails


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, message, email):
    (repo / 'content').write_text(message)
    git(repo, 'add', 'content')
    git(repo, '-c', 'user.name=Fixture', '-c', f'user.email={email}', 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD')


def test_imported_upstream_and_existing_fork_authors_are_excluded(tmp_path: Path):
    git(tmp_path, 'init', '-q')
    base = commit(tmp_path, 'base', 'base@example.test')
    git(tmp_path, 'switch', '-qc', 'upstream')
    upstream = commit(tmp_path, 'upstream', 'upstream@example.test')
    git(tmp_path, 'switch', '-qc', 'fork', base)
    previous = commit(tmp_path, 'old fork', 'old-fork@example.test')
    git(tmp_path, '-c', 'user.name=Fixture', '-c', 'user.email=merge@example.test', 'merge', '-qs', 'ours', 'upstream')
    commit(tmp_path, 'new fork', 'new-fork@example.test')
    assert new_author_emails(tmp_path, comparison_ref=previous, accepted_base=upstream) == ['new-fork@example.test']


def test_unincorporated_base_is_rejected(tmp_path: Path):
    git(tmp_path, 'init', '-q')
    base = commit(tmp_path, 'base', 'base@example.test')
    git(tmp_path, 'switch', '-qc', 'upstream')
    upstream = commit(tmp_path, 'upstream', 'upstream@example.test')
    git(tmp_path, 'switch', '-qc', 'fork', base)
    with pytest.raises(subprocess.CalledProcessError):
        new_author_emails(tmp_path, comparison_ref=base, accepted_base=upstream)


def test_missing_or_malformed_history_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match='comparison'):
        new_author_emails(tmp_path, comparison_ref=None, accepted_base=None)
    with pytest.raises(ValueError, match='full SHA'):
        new_author_emails(tmp_path, comparison_ref=None, accepted_base='main')
