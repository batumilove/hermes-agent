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
    from scripts.fork_provenance import source_distance

    return source_distance(repo, release, head)


def prepare_history(repo: Path, stamp: dict, *, upstream: str | None = None,
                    release: Release = BASELINE) -> str:
    """Acquire missing history explicitly, then apply the existing identity policy.

    Never change HEAD or the worktree. The official authority supplies the pinned
    release and upstream base; origin supplies only the immutable import source,
    whose projected tree and ancestry are checked by source_distance.
    """
    from scripts import fork_provenance as provenance
    from scripts.ci.prepare_upgrade_source import OFFICIAL

    head = _git(repo, "rev-parse", "HEAD")
    if stamp.get("commit") != head or stamp.get("dirty") or _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("image history preparation requires the stamped clean source HEAD")
    try:
        with provenance.offline_git():
            return package_version(repo, stamp, release)
    except (provenance.ProvenanceError, subprocess.CalledProcessError):
        pass
    authority = upstream or OFFICIAL
    provenance.git(repo, "fetch", "--filter=blob:none", "--no-tags", authority, f"refs/tags/{release.tag}")
    if (_git(repo, "rev-parse", "FETCH_HEAD") != release.tag_object
            or _git(repo, "rev-parse", "FETCH_HEAD^{commit}") != release.commit):
        raise ValueError("canonical image release differs from its pinned object or commit")
    if (repo / provenance.PROVENANCE_PATH).is_file():
        proof = provenance.validate_v2(json.loads(_git(repo, "show", f"{head}:{provenance.PROVENANCE_PATH}")))
        base = _git(repo, "show", f"{head}:{provenance.BASE_PATH}")
        if proof["upstream_base"] != base:
            raise ValueError("image import proof differs from recorded upstream base")
        provenance.git(repo, "fetch", "--filter=blob:none", "--no-tags", authority, base)
        if _git(repo, "rev-parse", "FETCH_HEAD") != base:
            raise ValueError("canonical upstream did not supply the recorded base")
        options = ["--filter=blob:none", "--no-tags"]
        if _git(repo, "rev-parse", "--is-shallow-repository") == "true":
            options.append("--unshallow")
        provenance.git(repo, "fetch", *options, "origin", proof["source_head"])
    elif _git(repo, "rev-parse", "--is-shallow-repository") == "true":
        provenance.git(repo, "fetch", "--unshallow", "--filter=blob:none", "--no-tags", "origin", head)
    with provenance.offline_git():
        return package_version(repo, stamp, release)


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
    stamp["packageVersion"] = prepare_history(args.repo, stamp)
    args.stamp.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
