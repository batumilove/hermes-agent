"""Bind source-image package metadata to an authenticated release and Git checkout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tomllib

from scripts.ci.prepare_upgrade_source import BASELINE, Release

_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def validate_metadata_version(value: str) -> str:
    """Accept canonical release versions and our provenance-bearing local versions."""
    if not isinstance(value, str) or not re.fullmatch(_VERSION + r"(?:\+[0-9]+\.g[0-9a-f]{40})?", value):
        raise ValueError("invalid image package version")
    return value


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8", timeout=30).strip()


def package_version(repo: Path, stamp: dict, release: Release = BASELINE) -> str:
    head = _git(repo, "rev-parse", "HEAD")
    if stamp.get("commit") != head or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("image stamp commit does not match source HEAD")
    if stamp.get("dirty") or _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("image identity requires clean tracked source")
    base = stamp.get("baseVersion")
    if base and base != "0.0.0" and type(stamp.get("distance")) is int and stamp["distance"] == 0 and stamp.get("displayVersion") == base:
        if not re.fullmatch(_VERSION, base):
            raise ValueError("invalid explicit image release version")
        return base
    if _git(repo, "rev-parse", release.tag_object) != release.tag_object:
        raise ValueError("missing authenticated image release object")
    if _git(repo, "rev-parse", f"{release.tag_object}^{{commit}}") != release.commit:
        raise ValueError("image release object does not bind the expected commit")
    distance = _source_distance(repo, release.commit, head)
    project = tomllib.loads(_git(repo, "show", f"{release.commit}:pyproject.toml"))["project"]
    base = project["version"]
    if project.get("name") != "hermes-agent" or not isinstance(base, str) or not re.fullmatch(_VERSION, base) or base == "0.0.0":
        raise ValueError("authenticated release has no usable Hermes package version")
    return validate_metadata_version(f"{base}+{distance}.g{head}")


def _source_distance(repo: Path, release: str, head: str) -> int:
    from scripts import fork_provenance as provenance

    if provenance.is_ancestor(repo, release, head):
        return int(_git(repo, "rev-list", "--count", f"{release}..{head}"))
    proof = provenance.validate_v2(json.loads(_git(repo, "show", f"{head}:{provenance.PROVENANCE_PATH}")))
    base = _git(repo, "show", f"{head}:{provenance.BASE_PATH}")
    # Acquisition belongs to the caller; never silently fetch mutable origin here.
    _git(repo, "cat-file", "-e", f"{proof['source_head']}^{{commit}}")
    _git(repo, "cat-file", "-e", f"{base}^{{commit}}")
    anchor = provenance.resolve_v2(repo, base, head, proof=proof)
    if not provenance.is_ancestor(repo, release, base):
        raise ValueError("image release is not an ancestor of the accepted upstream base")
    return (int(_git(repo, "rev-list", "--count", f"{release}..{proof['source_head']}"))
            + int(_git(repo, "rev-list", "--count", f"{anchor}..{head}")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--stamp", type=Path, required=True)
    args = parser.parse_args()
    if args.stamp.exists():
        stamp = json.loads(args.stamp.read_text(encoding="utf-8-sig"))
    else:
        from scripts.write_install_stamp import build_stamp
        stamp = build_stamp(commit=_git(args.repo, "rev-parse", "HEAD"),
                            branch=_git(args.repo, "rev-parse", "--abbrev-ref", "HEAD"),
                            commit_date=int(_git(args.repo, "show", "-s", "--format=%ct", "HEAD")),
                            dirty=False, source="ci", distribution="docker", update_mechanism="external")
    stamp["packageVersion"] = package_version(args.repo, stamp)
    args.stamp.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
