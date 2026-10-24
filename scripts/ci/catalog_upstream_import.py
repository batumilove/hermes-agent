"""Authenticate an official catalog import without re-admitting third-party pins."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import fork_provenance as provenance


def verify_import(repo: Path, head: str, comparison: str, official_tip: str) -> str:
    """Require an exact initial import and catalog bytes already accepted upstream."""
    official_tip = provenance.freeze_official_tip(repo, official_tip)
    head = provenance.git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    base = provenance.git(repo, "show", f"{head}:{provenance.BASE_PATH}")
    proof = provenance.validate_v2(json.loads(
        provenance.git(repo, "show", f"{head}:{provenance.PROVENANCE_PATH}")
    ))
    if not provenance.is_ancestor(repo, base, official_tip):
        raise provenance.ProvenanceError("catalog base is outside trusted official upstream history")
    previous_base = provenance.git(repo, "show", f"{comparison}:{provenance.BASE_PATH}")
    if previous_base == base:
        raise provenance.ProvenanceError("catalog import must advance the recorded upstream base")
    provenance.resolve_v2(repo, base, head, proof=proof)
    if provenance.projected_tree(repo, head) != proof["import_tree"]:
        raise provenance.ProvenanceError("catalog import head differs from its immutable source")
    expected = provenance.git(repo, "rev-parse", f"{base}:plugin-catalog")
    actual = provenance.git(repo, "rev-parse", f"{head}:plugin-catalog")
    if actual != expected:
        raise provenance.ProvenanceError("catalog tree differs from the official upstream catalog")
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--official-tip", required=True)
    args = parser.parse_args()
    try:
        base = verify_import(Path.cwd(), args.head, args.comparison, args.official_tip)
    except (provenance.ProvenanceError, ValueError) as error:
        print(f"official catalog import rejected: {error}", file=sys.stderr)
        return 1
    print(f"Verified exact official catalog and immutable import source at {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
