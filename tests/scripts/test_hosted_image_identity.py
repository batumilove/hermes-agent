"""Hosted image identity acquisition keeps source immutable and verifies history."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from hermes_cli import fork_provenance as provenance
from scripts.build import image_identity
from scripts.ci.prepare_upgrade_source import Release


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def disconnected(tmp_path):
    authority = tmp_path / 'authority'
    authority.mkdir()
    git(authority, 'init', '-q')
    git(authority, 'config', 'user.name', 'fixture')
    git(authority, 'config', 'user.email', 'fixture@example.invalid')
    (authority / 'pyproject.toml').write_text('[project]\nname="hermes-agent"\nversion="0.21.5"\n')
    git(authority, 'add', '.')
    git(authority, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'release')
    release_commit = git(authority, 'rev-parse', 'HEAD')
    git(authority, '-c', 'tag.gpgsign=false', 'tag', '-am', 'release', 'v2026.9.24')
    release = Release('v2026.9.24', git(authority, 'rev-parse', 'v2026.9.24'), release_commit)
    (authority / 'pyproject.toml').write_text('[project]\nname="hermes-agent"\nversion="0.0.0"\n')
    git(authority, 'commit', '-qam', 'upstream')
    base = git(authority, 'rev-parse', 'HEAD')
    (authority / '.github').mkdir()
    (authority / provenance.BASE_PATH).write_text(base + '\n')
    git(authority, 'add', '.')
    git(authority, 'commit', '-qm', 'source')
    source = git(authority, 'rev-parse', 'HEAD')
    tree = provenance.projected_tree(authority, source)
    git(authority, 'branch', 'source-history')
    git(authority, 'checkout', '--orphan', 'fork')
    proof = dict(version=2, upstream_base=base, source_head=source, import_tree=tree, fetch_depth=2)
    (authority / provenance.PROVENANCE_PATH).write_text(json.dumps(proof))
    git(authority, 'add', '.')
    git(authority, 'commit', '-qm', 'tree import')
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--depth=1', '--single-branch', '--no-tags', authority.as_uri(), str(checkout)], check=True)
    return checkout, authority, release


def test_shallow_disconnected_checkout_acquires_complete_verified_history(disconnected):
    repo, authority, release = disconnected
    head = git(repo, 'rev-parse', 'HEAD')
    stamp = {'commit': head}
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        image_identity.package_version(repo, stamp, release)
    version = image_identity.prepare_history(repo, stamp, upstream=str(authority), release=release)
    assert version == f'0.21.5+2.g{head}'
    assert git(repo, 'rev-parse', 'HEAD') == head
    assert not git(repo, 'status', '--porcelain')
    assert git(repo, 'rev-parse', '--is-shallow-repository') == 'false'
    with provenance.offline_git():
        assert image_identity.package_version(repo, stamp, release) == version


@pytest.mark.parametrize('damage', ['tag', 'projection', 'dirty', 'commit'])
def test_acquisition_rejects_unbound_identity(disconnected, damage):
    repo, authority, release = disconnected
    stamp = {'commit': git(repo, 'rev-parse', 'HEAD')}
    if damage == 'tag':
        release = Release(release.tag, 'a' * 40, release.commit)
    elif damage == 'projection':
        git(repo, 'config', 'user.name', 'fixture')
        git(repo, 'config', 'user.email', 'fixture@example.invalid')
        proof_path = repo / provenance.PROVENANCE_PATH
        proof = json.loads(proof_path.read_text())
        proof['import_tree'] = 'a' * 40
        proof_path.write_text(json.dumps(proof))
        git(repo, 'commit', '-qam', 'bad proof')
        stamp['commit'] = git(repo, 'rev-parse', 'HEAD')
    elif damage == 'dirty':
        (repo / 'pyproject.toml').write_text('dirty')
    else:
        stamp['commit'] = 'b' * 40
    head = git(repo, 'rev-parse', 'HEAD')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        image_identity.prepare_history(repo, stamp, upstream=str(authority), release=release)
    assert git(repo, 'rev-parse', 'HEAD') == head


def test_application_build_and_publish_stamps_populate_package_version():
    workflow = Path('.github/workflows/docker.yml').read_text()
    assert workflow.count('python3 -m scripts.build.image_identity --repo . --stamp install-stamp.json') == 2
