"""Rehearse fork upgrades on the exact reviewed application tree's real source ancestry.

Linear import reviews retain their normal checkout in every other CI job. This
upgrade-only routing authenticates the import and release baseline. Exact import
mirrors select their immutable source; ordinary descendants retain their current
application changes while rehearsing upgrades from the same verified release.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_cli import fork_provenance as provenance

FORK = "batumilove/hermes-agent"
OFFICIAL = "https://github.com/NousResearch/hermes-agent.git"


@dataclass(frozen=True)
class Release:
    tag: str
    tag_object: str
    commit: str


BASELINE = Release(
    "v2026.9.24", "e3dd27ee2d8b011737a4eea8e3eb3d711ab78690",
    "f97608f178d1ffeca59860195ab7da295f7c8e5f",
)


def _fetch_official(repo: Path, upstream: str, reference: str) -> str:
    provenance.git(repo, "fetch", "--filter=blob:none", "--no-tags", upstream, reference)
    return provenance.git(repo, "rev-parse", "FETCH_HEAD")


def prepare_upgrade_source(
    repo: Path, repository: str, *, upstream: str = OFFICIAL, release: Release = BASELINE,
) -> dict | None:
    """Authenticate before checkout; upstream and no-proof runs keep their existing behavior."""
    repo = repo.resolve()
    if repository != FORK or not (repo / provenance.PROVENANCE_PATH).is_file():
        return None
    if provenance.git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise provenance.ProvenanceError("upgrade source routing requires a clean tracked checkout")
    review = provenance.git(repo, "rev-parse", "HEAD")
    proof = provenance.validate_v2(json.loads(provenance.git(repo, "show", f"{review}:{provenance.PROVENANCE_PATH}")))
    base = provenance.git(repo, "show", f"{review}:{provenance.BASE_PATH}")
    if proof["upstream_base"] != base:
        raise provenance.ProvenanceError("upgrade proof upstream base differs from the recorded base")
    if _fetch_official(repo, upstream, base) != base:
        raise provenance.ProvenanceError("canonical upstream did not provide the recorded base object")
    provenance.resolve_v2(repo, base, review, proof=proof)
    if _fetch_official(repo, upstream, f"refs/tags/{release.tag}") != release.tag_object:
        raise provenance.ProvenanceError("canonical upgrade release tag differs from its pinned object")
    if provenance.git(repo, "rev-parse", "FETCH_HEAD^{commit}") != release.commit:
        raise provenance.ProvenanceError("canonical upgrade release differs from its pinned commit")
    source = proof["source_head"]
    if not provenance.is_ancestor(repo, release.commit, base) or not provenance.is_ancestor(repo, release.commit, source):
        raise provenance.ProvenanceError("upgrade release is not an ancestor of trusted upstream and source")
    checkout = review
    if provenance.projected_tree(repo, review) == proof["import_tree"]:
        provenance.git(repo, "checkout", "--detach", source)
        checkout = source
    return {"review_head": review, "source_head": source, "import_tree": proof["import_tree"],
            "upstream_base": base, "release_tag": release.tag, "release_commit": release.commit,
            "checkout_head": checkout}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = prepare_upgrade_source(args.repo, args.repository)
    except (provenance.ProvenanceError, json.JSONDecodeError) as error:
        print(f"Upgrade source verification failed: {error}", file=sys.stderr)
        return 1
    if receipt is not None:
        with args.github_env.open("a", encoding="utf-8") as output:
            output.write(f"HERMES_E2E_UPGRADE_BASE={receipt['release_commit']}\n")
        print("Upgrade rehearsal with authenticated release baseline:")
        print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
