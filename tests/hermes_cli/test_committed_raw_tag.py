"""Committed exact-byte custody; synthetic anchors never become product trust."""
import hashlib
import subprocess

import pytest

from hermes_cli import upstream_compatibility as compat
from tests.hermes_cli.test_upstream_release_custody import git

PATH = '.github/upstream-proof/v0.22.0.tag'


def artifact(repo, receipt):
    raw = subprocess.check_output(['git', 'cat-file', 'tag', receipt['tag_object']], cwd=repo)
    path = repo / PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    git(repo, 'add', PATH)
    git(repo, 'commit', '-qm', 'exact artifact')
    return path, raw


def remove_native(repo, receipt):
    git(repo, 'tag', '-d', receipt['release_tag'])
    p = repo / '.git/objects' / receipt['tag_object'][:2] / receipt['tag_object'][2:]
    assert p.is_file()
    p.unlink()


def test_artifact_without_native(release_repo):
    repo, receipt = release_repo
    artifact(repo, receipt)
    remove_native(repo, receipt)
    assert compat.validate_receipt(repo, receipt) == receipt['release_tag'][1:]


@pytest.mark.parametrize('fault', ['absent', 'truncated', 'altered', 'uncommitted', 'working_missing'])
def test_artifact_faults_fail_closed(release_repo, fault):
    repo, receipt = release_repo
    path, raw = artifact(repo, receipt)
    remove_native(repo, receipt)
    if fault == 'absent':
        git(repo, 'rm', PATH)
        git(repo, 'commit', '-qm', 'remove')
    elif fault == 'working_missing':
        path.unlink()
    elif fault == 'uncommitted':
        git(repo, 'rm', '--cached', PATH)
        git(repo, 'commit', '-qm', 'uncommitted')
    else:
        path.write_bytes(raw[:-1] if fault == 'truncated' else raw + b'x')
        git(repo, 'add', PATH)
        git(repo, 'commit', '-qm', 'invalid bytes')
    assert compat.validate_receipt(repo, receipt) is None


def test_present_bad_artifact_overrides_valid_native(release_repo):
    repo, receipt = release_repo
    path, raw = artifact(repo, receipt)
    path.write_bytes(raw + b'\n')
    assert compat.validate_receipt(repo, receipt) is None


@pytest.mark.parametrize('fault', ['duplicate', 'type', 'name', 'target', 'missing', 'tab'])
def test_exact_hash_does_not_replace_header_validation(release_repo, monkeypatch, fault):
    repo, receipt = release_repo
    path, raw = artifact(repo, receipt)
    remove_native(repo, receipt)
    first = raw.split(b'\n', 1)[0]
    transforms = {
        'duplicate': lambda: first + b'\n' + raw,
        'type': lambda: raw.replace(b'type commit', b'type tree', 1),
        'name': lambda: raw.replace(b'tag ' + receipt['release_tag'].encode(), b'tag wrong', 1),
        'target': lambda: raw.replace(first, b'object ' + b'0' * 40, 1),
        'missing': lambda: raw.replace(b'type commit\n', b'', 1),
        'tab': lambda: raw.replace(b'object ', b'object\t', 1),
    }
    raw = transforms.get(fault, transforms['tab'])()
    path.write_bytes(raw)
    receipt = dict(receipt, tag_object=hashlib.sha1(b'tag ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())
    anchor = {k: receipt[k] for k in compat.REVIEWED_RELEASES[0]}
    monkeypatch.setattr(compat, 'REVIEWED_RELEASES', (anchor,))
    (repo / compat.RECEIPT_PATH).write_text(__import__('json').dumps(receipt))
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'malformed matching hash')
    assert compat.validate_receipt(repo, receipt) is None


def test_present_native_conflict_no_artifact_fallback(release_repo, monkeypatch):
    repo, receipt = release_repo
    artifact(repo, receipt)
    original = compat._git
    def conflicting(repo, *args, **kwargs):
        if args == ('cat-file', '-t', receipt['tag_object']):
            return 'blob'
        return original(repo, *args, **kwargs)
    monkeypatch.setattr(compat, '_git', conflicting)
    assert compat.validate_receipt(repo, receipt) is None


@pytest.mark.parametrize('field', ['upstream_base', 'source_head', 'import_tree'])
def test_other_objects_cannot_be_supplied_by_artifact(release_repo, monkeypatch, field):
    import json
    repo, receipt = release_repo
    artifact(repo, receipt)
    remove_native(repo, receipt)
    receipt = dict(receipt, proof=dict(receipt['proof'], **{field: '0' * 40}))
    (repo / compat.PROVENANCE_PATH).write_text(json.dumps(receipt['proof']))
    (repo / compat.RECEIPT_PATH).write_text(json.dumps(receipt))
    if field == 'upstream_base':
        (repo / '.github/upstream-base').write_text('0' * 40)
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'missing proof root')
    assert compat.validate_receipt(repo, receipt) is None
