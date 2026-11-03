"""Choose the accepted upstream tree only for a fork reconciliation transition."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_cli import fork_provenance


def accepted_upstream(repo: Path, tip: str = "HEAD", *, official_tip: str | None = None) -> str | None:
    path = repo / ".github" / "upstream-base"
    if not path.is_file():
        return None
    accepted = path.read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", accepted):
        raise ValueError("accepted upstream base must be a full SHA")
    fork_provenance.accepted_anchor(repo, accepted, tip, official_tip=official_tip)
    return accepted


def verify_canonical_promotion(repo: Path, tip: str, canonical_tip: str) -> None:
    """A caller supplies a separately fetched trusted canonical ref, never PR metadata."""
    canonical = fork_provenance.git(repo, "rev-parse", "--verify", f"{canonical_tip}^{{commit}}")
    if not fork_provenance.is_ancestor(repo, canonical, tip):
        raise fork_provenance.ProvenanceError("canonical promotion must retain accepted ancestry")
    expected = fork_provenance.git(repo, "rev-parse", f"{canonical}^{{tree}}")
    if fork_provenance.git(repo, "rev-parse", f"{tip}^{{tree}}") != expected:
        raise fork_provenance.ProvenanceError("canonical promotion must preserve the complete accepted tree")


def comparison_base(repo: Path, comparison: str, tip: str = "HEAD", *, official_tip: str | None = None,
                    canonical_tip: str | None = None) -> str:
    official_tip = fork_provenance.freeze_official_tip(repo, official_tip)
    comparison = fork_provenance.git(repo, "rev-parse", "--verify", f"{comparison}^{{commit}}")
    tip = fork_provenance.git(repo, "rev-parse", "--verify", f"{tip}^{{commit}}")
    accepted = accepted_upstream(repo, tip, official_tip=official_tip)
    if accepted is None:
        return comparison
    anchor = fork_provenance.accepted_anchor(repo, accepted, tip, official_tip=official_tip)
    # Once the target branch contains the import, keep its ordinary per-PR ratchet.
    if fork_provenance.comparison_anchor(repo, accepted, comparison, anchor) is not None:
        return comparison
    if anchor != accepted:
        proof = fork_provenance.load_v2(repo / fork_provenance.PROVENANCE_PATH)
        if fork_provenance.projected_tree(repo, tip) != proof["import_tree"]:
            if canonical_tip is None:
                raise fork_provenance.ProvenanceError(
                    "initial import head differs from import_tree; regenerate against the advanced target"
                )
            verify_canonical_promotion(repo, tip, canonical_tip)
    return accepted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--tip", default="HEAD")
    parser.add_argument("--official-tip", help="Trusted, separately fetched official upstream ref")
    parser.add_argument("--canonical-tip", help="Trusted canonical ref for an exact-tree promotion")
    args = parser.parse_args()
    print(comparison_base(Path.cwd(), args.comparison, args.tip, official_tip=args.official_tip,
                          canonical_tip=args.canonical_tip))


if __name__ == "__main__":
    main()
