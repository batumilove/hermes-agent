"""Verify an upstream tree import independently of squash/rebase commit identities.

Only the committed provenance file is projected away. All other paths, modes,
and object identities must agree with the immutable reconciliation source.
This module uses the standard library so contributor CI needs no bootstrap.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

PROVENANCE_PATH = ".github/upstream-sync-provenance.json"
BASE_PATH = ".github/upstream-base"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_OFFLINE = ContextVar("fork_provenance_offline", default=False)
_FIELDS = {"version", "upstream_base", "source_head", "import_tree", "fetch_depth"}


class ProvenanceError(ValueError):
    """The recorded import does not establish the claimed upstream relationship."""


@contextmanager
def offline_git():
    """Verify local evidence without explicit or partial-clone network fetches."""
    token = _OFFLINE.set(True)
    try:
        yield
    finally:
        _OFFLINE.reset(token)


def _read_environment(env=None):
    if not _OFFLINE.get():
        return env
    # protocol.allow also covers Git versions predating GIT_NO_LAZY_FETCH.
    result = dict(os.environ if env is None else env)
    count = int(result.get("GIT_CONFIG_COUNT", "0"))
    result.update({"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_CONFIG_COUNT": str(count + 1),
                   f"GIT_CONFIG_KEY_{count}": "protocol.allow",
                   f"GIT_CONFIG_VALUE_{count}": "never"})
    return result


def git(repo: Path, *args: str, env=None, binary=False, input_data=None):
    result = subprocess.run(
        ["git", *args], cwd=repo, env=_read_environment(env), capture_output=True,
        text=not binary, input=input_data, timeout=3 if _OFFLINE.get() else 120, check=False,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace") if binary else result.stderr
        raise ProvenanceError(detail.strip().splitlines()[0] if detail.strip() else "Git provenance check failed")
    return result.stdout if binary else result.stdout.strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo, env=_read_environment(), capture_output=True, text=True, timeout=3 if _OFFLINE.get() else 120, check=False,
    )
    if result.returncode not in (0, 1):
        raise ProvenanceError(result.stderr.strip() or "could not inspect Git ancestry")
    return result.returncode == 0


def validate_v2(raw: object) -> dict:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 2:
        raise ProvenanceError("tree-import provenance version must be 2")
    if set(raw) != _FIELDS:
        raise ProvenanceError(f"tree-import provenance fields must be {sorted(_FIELDS)}")
    for field in ("upstream_base", "source_head", "import_tree"):
        if not isinstance(raw[field], str) or not _SHA.fullmatch(raw[field]):
            raise ProvenanceError(f"tree-import provenance {field} must be a full lowercase SHA")
    if type(raw["fetch_depth"]) is not int or not 1 <= raw["fetch_depth"] <= 1024:
        raise ProvenanceError("tree-import provenance fetch_depth must be an integer from 1 to 1024")
    return dict(raw)


def load_v2(path: Path) -> dict:
    try:
        return validate_v2(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"could not read tree-import provenance: {error}") from error


def projected_tree(repo: Path, revision: str) -> str:
    """Project committed content using an isolated Git index, never the worktree."""
    with tempfile.TemporaryDirectory(prefix="hermes-import-index-") as scratch:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(scratch) / "index"))
        git(repo, "read-tree", f"{revision}^{{tree}}", env=env)
        git(repo, "update-index", "--force-remove", "--", PROVENANCE_PATH, env=env)
        return git(repo, "write-tree", env=env)


def fetch_source(repo: Path, source: str, depth: int, *, required_ancestor: str | None = None) -> str:
    try:
        git(repo, "cat-file", "-e", f"{source}^{{commit}}")
        if required_ancestor is not None:
            git(repo, "cat-file", "-e", f"{required_ancestor}^{{commit}}")
    except ProvenanceError:
        if _OFFLINE.get():
            raise
        options = ["--filter=blob:none", "--no-tags"]
        if git(repo, "rev-parse", "--is-shallow-repository") == "true":
            options.insert(0, f"--depth={depth}")
        git(repo, "fetch", *options, "origin", source)
    return source


def _tree_entries(payload: bytes) -> dict:
    entries = {}
    offset = 0
    while offset < len(payload):
        space = payload.index(b" ", offset)
        nul = payload.index(b"\0", space)
        entries[payload[space + 1:nul]] = (payload[offset:space], payload[nul + 1:nul + 21])
        offset = nul + 21
    return entries


def _read_trees(repo: Path, trees: list[str]) -> dict:
    """Read one bounded batch of tree objects; Git names are bytes, not line-delimited."""
    if not trees:
        return {}
    payload = git(repo, "cat-file", "--batch", binary=True,
                  input_data=("\n".join(dict.fromkeys(trees)) + "\n").encode())
    result = {}
    offset = 0
    while offset < len(payload):
        end = payload.index(b"\n", offset)
        oid, kind, size = payload[offset:end].split()
        if kind != b"tree":
            raise ProvenanceError("import history references a non-tree object")
        length = int(size)
        result[oid.decode()] = _tree_entries(payload[end + 1:end + 1 + length])
        offset = end + 2 + length
    return result


def _candidate_github(roots: dict, expected: dict) -> dict:
    candidates = {}
    for tree, entries in roots.items():
        other = {name: value for name, value in entries.items() if name != b".github"}
        if other == expected:
            candidates[tree] = entries.get(b".github")
    return candidates


def _github_matches(entry, expected_entry, github_trees: dict, expected_files: dict) -> bool:
    if entry is None:
        return expected_entry is None
    if entry[0] != b"40000":
        return entry == expected_entry
    files = dict(github_trees[entry[1].hex()])
    files.pop(b"upstream-sync-provenance.json", None)
    return files == expected_files


def find_import_ancestor(repo: Path, head: str, import_tree: str) -> str | None:
    """Find an exact projected ancestor, batching history reads to bound memory/I/O.

    Unchanged root entries and .github entries cheaply reject candidates. The
    final acceptance always uses the canonical isolated-index projection.
    """
    history = [line.split() for line in git(repo, "log", "--format=%H %T", head).splitlines()]
    expected = _read_trees(repo, [import_tree])[import_tree]
    expected_entry = expected.pop(b".github", None)
    expected_files = {}
    if expected_entry is not None and expected_entry[0] == b"40000":
        oid = expected_entry[1].hex()
        expected_files = _read_trees(repo, [oid])[oid]
    for offset in range(0, len(history), 256):
        batch = history[offset:offset + 256]
        roots = _read_trees(repo, [tree for _, tree in batch])
        candidates = _candidate_github(roots, expected)
        github_trees = _read_trees(repo, [entry[1].hex() for entry in candidates.values()
                                        if entry is not None and entry[0] == b"40000"])
        for commit, tree in batch:
            if tree in candidates and _github_matches(candidates[tree], expected_entry, github_trees, expected_files):
                if projected_tree(repo, commit) == import_tree:
                    return commit
    return None


def resolve_v2(repo: Path, base: str, head: str = "HEAD", *, proof=None, base_relative=BASE_PATH) -> str:
    proof = validate_v2(proof) if proof is not None else load_v2(repo / PROVENANCE_PATH)
    head = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    if proof["upstream_base"] != base:
        raise ProvenanceError("tree-import provenance upstream base does not match the recorded base")
    source = fetch_source(repo, proof["source_head"], proof["fetch_depth"], required_ancestor=base)
    if not is_ancestor(repo, base, source):
        raise ProvenanceError("upstream base is not an ancestor of the recorded sync source")
    if git(repo, "show", f"{source}:{base_relative}") != base:
        raise ProvenanceError("recorded sync source does not contain the recorded upstream base")
    if projected_tree(repo, source) != proof["import_tree"]:
        raise ProvenanceError("recorded sync source projected tree differs from import_tree")
    ancestor = find_import_ancestor(repo, head, proof["import_tree"])
    if ancestor is None:
        raise ProvenanceError("checked head has no ancestor with the recorded import projected tree")
    return ancestor


def accepted_anchor(repo: Path, base: str, head: str = "HEAD", *, official_tip: str | None = None) -> str:
    official_tip = freeze_official_tip(repo, official_tip)
    head = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    try:
        git(repo, "cat-file", "-e", f"{base}^{{commit}}")
        direct = is_ancestor(repo, base, head)
    except ProvenanceError:
        direct = False
    if direct:
        anchor = base
    else:
        if not (repo / PROVENANCE_PATH).is_file():
            raise ProvenanceError("accepted upstream base is not an ancestor of the checked commit")
        anchor = resolve_v2(repo, base, head)
    if official_tip is not None and not is_ancestor(repo, base, official_tip):
        raise ProvenanceError("accepted upstream base is not in the trusted official upstream history")
    return anchor


def freeze_official_tip(repo: Path, reference: str | None) -> str | None:
    """Snapshot a trusted ref before a source fetch can replace FETCH_HEAD."""
    return git(repo, "rev-parse", "--verify", f"{reference}^{{commit}}") if reference is not None else None


def comparison_anchor(repo: Path, base: str, comparison: str, head_anchor: str) -> str | None:
    if is_ancestor(repo, base, comparison):
        return base
    if head_anchor == base:
        return None
    proof = load_v2(repo / PROVENANCE_PATH)
    # HEAD may revisit the import tree. The trusted target independently
    # determines whether this upstream baseline has already been incorporated.
    return find_import_ancestor(repo, comparison, proof["import_tree"])


def source_distance(repo: Path, release: str, head: str) -> int:
    """Count source and fork commits only after verifying the accepted import."""
    if is_ancestor(repo, release, head):
        return int(git(repo, "rev-list", "--count", f"{release}..{head}"))
    proof = validate_v2(json.loads(git(repo, "show", f"{head}:{PROVENANCE_PATH}")))
    base = git(repo, "show", f"{head}:{BASE_PATH}")
    # Acquisition belongs to the caller, including for shallow source installs.
    git(repo, "cat-file", "-e", f"{proof['source_head']}^{{commit}}")
    git(repo, "cat-file", "-e", f"{base}^{{commit}}")
    anchor = resolve_v2(repo, base, head, proof=proof)
    if not is_ancestor(repo, release, base):
        raise ProvenanceError("release is not an ancestor of the accepted upstream base")
    return (int(git(repo, "rev-list", "--count", f"{release}..{proof['source_head']}"))
            + int(git(repo, "rev-list", "--count", f"{anchor}..{head}")))
