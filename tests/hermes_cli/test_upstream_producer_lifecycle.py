"""Producer proposals never substitute for fresh committed runtime custody."""
import json
import subprocess
import sys

import pytest

from hermes_cli import upstream_compatibility as compat
from tests.hermes_cli.test_upstream_release_custody import git

pytest_plugins = ['tests.hermes_cli.test_upstream_release_custody']


def capture(receipt):
    a = receipt
    return {'records': [dict(id=a['release_id'], tag_name=a['release_tag'], published_at=a['published_at'], draft=False, prerelease=False, html_url=f"https://github.com/{a['repository']}/releases/tag/{a['release_tag']}"), dict(ref=f"refs/tags/{a['release_tag']}", object=dict(sha=a['tag_object'], type='tag')), dict(sha=a['tag_object'], tag=a['release_tag'], object=dict(sha=a['release_commit'], type='commit'))]}


@pytest.mark.parametrize('mode', ['first', 'replacement'])
def test_producer_write_commit_fresh_admission(release_repo, mode):
    repo, old = release_repo
    if mode == 'first':
        git(repo, 'rm', compat.RECEIPT_PATH)
        git(repo, 'commit', '-qm', 'no prior receipt')
    else:
        # A new committed metadata declaration makes preceding custody stale.
        proof = dict(old['proof'], fetch_depth=9)
        (repo / compat.PROVENANCE_PATH).write_text(json.dumps(proof))
        git(repo, 'add', compat.PROVENANCE_PATH)
        git(repo, 'commit', '-qm', 'new proof')
        assert compat.validate_receipt(repo, old) is None
    before = git(repo, 'rev-parse', 'HEAD')
    proposed = compat.produce_receipt(repo, capture(old))
    assert git(repo, 'rev-parse', 'HEAD') == before
    assert compat.validate_receipt(repo, proposed) is None
    (repo / compat.RECEIPT_PATH).write_text(json.dumps(proposed))
    assert compat.validate_receipt(repo, proposed) is None
    git(repo, 'add', compat.RECEIPT_PATH)
    git(repo, 'commit', '-qm', 'commit proposed receipt')
    anchor = {k: old[k] for k in compat.REVIEWED_RELEASES[0]}
    program = """import json, sys
from pathlib import Path
from hermes_cli import upstream_compatibility as c
c.REVIEWED_RELEASES = (json.loads(sys.argv[2]),)
p = Path(sys.argv[1])
assert c.validate_receipt(p, json.loads((p / c.RECEIPT_PATH).read_text())) == sys.argv[3]
"""
    subprocess.run([sys.executable, '-c', program, str(repo), json.dumps(anchor), old['release_tag'][1:]], check=True)


@pytest.mark.parametrize('fault', ['empty', 'draft', 'ref', 'target'])
def test_malformed_capture_cannot_construct(release_repo, fault):
    repo, old = release_repo
    evidence = capture(old)
    if fault == 'empty':
        evidence['records'] = []
    elif fault == 'draft':
        evidence['records'][0]['draft'] = True
    elif fault == 'ref':
        evidence['records'][1]['object']['type'] = 'commit'
    else:
        evidence['records'][2]['object']['sha'] = '0' * 40
    before = (repo / compat.RECEIPT_PATH).read_bytes()
    with pytest.raises((ValueError, IndexError, KeyError, TypeError)):
        compat.produce_receipt(repo, evidence)
    assert (repo / compat.RECEIPT_PATH).read_bytes() == before
