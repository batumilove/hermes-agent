"""Offline, source-only upstream release boundary for plugin admission.

This does not rename a fork release or prove compatibility of subsequent fork
changes. Published identities below are maintainer-reviewed trust anchors, not
versions supplied by a receipt. Missing local Git objects fail closed.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts.fork_provenance import PROVENANCE_PATH, validate_v2

RECEIPT_PATH = ".github/upstream-compatibility.json"
from hermes_cli.upstream_release_anchors import REVIEWED_RELEASES


def _anchor(receipt: dict) -> dict:
    for anchor in REVIEWED_RELEASES:
        if all(type(receipt.get(k)) is type(v) and receipt[k] == v for k, v in anchor.items()):
            return anchor
    raise ValueError("untrusted published release")


def _tag_proof(repo: Path, anchor: dict) -> None:
    import hashlib

    oid = anchor["tag_object"]
    # Fixed representation, not a receipt-controlled path or a new anchor.
    path = ".github/upstream-proof/v0.22.0.tag"
    working = repo / path
    entries = _git(repo, "ls-tree", "HEAD", "--", path)
    artifact = None
    if entries or working.exists() or working.is_symlink():
        if not entries or working.is_symlink() or not working.is_file():
            raise ValueError("tag artifact custody mismatch")
        artifact = _git(repo, "show", f"HEAD:{path}", preserve=True).encode("utf-8")
        if working.read_bytes() != artifact:
            raise ValueError("tag artifact custody mismatch")
        _check_tag_bytes(artifact, anchor)
    # cat-file --batch-check distinguishes absent from present wrong-type objects.
    # Errors/corruption do not become absence and never select a fallback.
    info = _git(repo, "cat-file", "--batch-check", input_text=oid + "\n")
    if info == oid + " missing":
        if artifact is None:
            raise ValueError("annotated tag required")
        return
    if _git(repo, "cat-file", "-t", oid) != "tag":
        raise ValueError("annotated tag required")
    native = _git(repo, "cat-file", "tag", oid, preserve=True).encode("utf-8")
    _check_tag_bytes(native, anchor)
    if artifact is not None and native != artifact:
        raise ValueError("tag representations disagree")
    if _git(repo, "rev-parse", f"{oid}^{{commit}}") != anchor["release_commit"]:
        raise ValueError("peeled release mismatch")


def _check_tag_bytes(raw: bytes, anchor: dict) -> None:
    import hashlib

    if hashlib.sha1(b"tag " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest() != anchor["tag_object"]:
        raise ValueError("tag object hash mismatch")
    if b"\n\n" not in raw:
        raise ValueError("tag headers missing")
    headers = raw.split(b"\n\n", 1)[0].decode("utf-8").split("\n")
    for key, value in (("object", anchor["release_commit"]), ("type", "commit"), ("tag", anchor["release_tag"])):
        matches = [line for line in headers if line.split(maxsplit=1)[0:1] == [key]]
        if matches != [f"{key} {value}"]:
            raise ValueError("annotated tag mismatch")


def _git(repo: Path, *args: str, preserve: bool = False, input_text: str | None = None) -> str:
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1",
               GIT_ALLOW_PROTOCOL="", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True,
        text=not preserve, encoding=None if preserve else "utf-8", errors=None if preserve else "strict",
        timeout=10, check=False, input=input_text,
    )
    if result.returncode:
        raise ValueError("local Git proof unavailable")
    return result.stdout.decode("utf-8") if preserve else result.stdout.strip()


def _ancestor(repo: Path, first: str, last: str) -> None:
    _git(repo, "merge-base", "--is-ancestor", first, last)


def _projection(repo: Path, revision: str) -> list[str]:
    # Compare immutable recursive entries (mode, kind, oid, path), not working
    # files. Unlike the governance index writer, this never writes Git objects.
    return [entry for entry in _git(repo, "ls-tree", "-r", "-z", revision).split("\0")
            if entry and entry.split("\t", 1)[1] != PROVENANCE_PATH]


def _validate_proposal(repo: Path, receipt: object) -> str:
    """Validate offline proof only; a proposal is never runtime admission."""
    if not isinstance(receipt, dict):
        raise ValueError("invalid receipt schema")
    anchor = _anchor(receipt)
    if set(receipt) != set(anchor) | {"schema", "proof", "squash_commit"}:
        raise ValueError("invalid receipt schema")
    if type(receipt["schema"]) is not int or receipt["schema"] != 1:
        raise ValueError("invalid receipt schema")
    _tag_proof(repo, anchor)
    proof = validate_v2(receipt["proof"])
    squash = receipt["squash_commit"]
    import re
    if not isinstance(squash, str) or not re.fullmatch("[0-9a-f]{40}", squash):
        raise ValueError("invalid squash")
    # Both committed and working metadata must agree: an edited/stale import
    # declaration cannot silently inherit the preceding import's admission.
    committed = json.loads(_git(repo, "show", f"HEAD:{PROVENANCE_PATH}"))
    if proof != validate_v2(committed) or proof != validate_v2(json.loads((repo / PROVENANCE_PATH).read_text(encoding="utf-8-sig"))):
        raise ValueError("stale import proof")
    base = proof["upstream_base"]
    if _git(repo, "show", "HEAD:.github/upstream-base") != base or (repo / ".github/upstream-base").read_text(encoding="utf-8-sig").strip() != base:
        raise ValueError("stale upstream base")
    source = proof["source_head"]
    _ancestor(repo, receipt["release_commit"], base)
    _ancestor(repo, base, source)
    _ancestor(repo, squash, "HEAD")
    if _git(repo, "show", f"{source}:.github/upstream-base") != base:
        raise ValueError("source base mismatch")
    expected = _projection(repo, proof["import_tree"])
    if not expected or _projection(repo, source) != expected or _projection(repo, squash) != expected:
        raise ValueError("import tree mismatch")
    return receipt["release_tag"][1:]


def _validate(repo: Path, receipt: object) -> str:
    committed_receipt = _git(repo, "show", f"HEAD:{RECEIPT_PATH}", preserve=True)
    working_receipt = (repo / RECEIPT_PATH).read_bytes().decode("utf-8")
    if committed_receipt != working_receipt or json.loads(committed_receipt) != receipt:
        raise ValueError("receipt custody mismatch")
    return _validate_proposal(repo, receipt)


def validate_receipt(repo: Path, receipt: object) -> str | None:
    """Return only a validated boundary; invalid/unavailable proof adds nothing."""
    try:
        return _validate(repo, receipt)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def produce_receipt(repo: Path, release_evidence: dict) -> dict:
    """Explicit offline producer; reviewed official API capture is mandatory."""
    records = release_evidence["records"]
    release, ref, tag = records[:3]
    anchor = _anchor(dict(repository="NousResearch/hermes-agent", release_id=release["id"],
                          release_tag=release["tag_name"], release_commit=tag["object"]["sha"],
                          tag_object=tag["sha"], published_at=release["published_at"]))
    if (release["id"] != anchor["release_id"] or release["draft"] is not False
            or release["prerelease"] is not False or release["tag_name"] != anchor["release_tag"]
            or release["published_at"] != anchor["published_at"]
            or release["html_url"] != f"https://github.com/{anchor['repository']}/releases/tag/{anchor['release_tag']}"
            or ref["ref"] != f"refs/tags/{anchor['release_tag']}"
            or ref["object"]["sha"] != anchor["tag_object"] or ref["object"]["type"] != "tag"
            or tag["sha"] != anchor["tag_object"] or tag["tag"] != anchor["release_tag"]
            or tag["object"]["sha"] != anchor["release_commit"] or tag["object"]["type"] != "commit"):
        raise ValueError("release evidence does not match reviewed official anchor")
    proof = validate_v2(json.loads((repo / PROVENANCE_PATH).read_text(encoding="utf-8-sig")))
    # Canonical governance lookup, with an explicitly network-disabled environment
    # is intentionally not used: its helper can fetch/write objects. Search only
    # locally available immutable ancestry, using the same projection contract.
    expected = _projection(repo, proof["import_tree"])
    squash = next((sha for sha in _git(repo, "rev-list", "HEAD").splitlines()
                   if _projection(repo, sha) == expected), None)
    receipt = dict(anchor, schema=1, proof=proof, squash_commit=squash)
    _validate_proposal(repo, receipt)
    # Only a proposal: runtime still requires byte-exact committed custody.
    return receipt


def _executing_repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _matching_source_stamp(repo: Path, stamp_path: Path) -> bool:
    from hermes_cli.steward import read_install_stamp

    if stamp_path != repo / "install-stamp.json":
        return False
    stamp = read_install_stamp(repo)
    expected = {"updateMechanism": "self", "source": "git", "payload": "bootstrap"}
    return (all(stamp.get(key) == value for key, value in expected.items())
            and stamp.get("commit") == _git(repo, "rev-parse", "HEAD"))


def verified_upstream_version() -> str | None:
    """Read an explicit receipt from the executing source tree, never a profile."""
    from hermes_cli.version_info import _resolve_stamp_file
    repo = _executing_repo()
    try:
        # Source completion stamps mutable checkouts too. Only its matching
        # identity may use source proof; packaged builds keep their own contract.
        stamp = _resolve_stamp_file()
        if stamp is not None and not _matching_source_stamp(repo, stamp):
            return None
        return validate_receipt(repo, json.loads((repo / RECEIPT_PATH).read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
