"""List fork authors while excluding the accepted upstream and PR base histories."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_cli import fork_provenance

def new_author_emails(
    repo: Path, *, comparison_ref: str | None, accepted_base: str | None, official_tip: str | None = None
) -> list[str]:
    excluded = []
    if comparison_ref:
        excluded.append(comparison_ref)
    if accepted_base:
        if not re.fullmatch(r"[0-9a-f]{40}", accepted_base):
            raise ValueError("accepted upstream base must be a full SHA")
        try:
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", accepted_base, "HEAD"],
                cwd=repo, capture_output=True, check=True, timeout=120,
            )
        except subprocess.CalledProcessError:
            # Preserve legacy invalid-history errors when no proof is configured.
            if not (repo / fork_provenance.PROVENANCE_PATH).is_file():
                raise
        anchor = fork_provenance.accepted_anchor(repo, accepted_base, official_tip=official_tip)
        excluded.append(accepted_base)
        # Keep the initial import's fork author in the PR check. Only an
        # already-incorporated import is a baseline for subsequent authors.
        if comparison_ref:
            incorporated = fork_provenance.comparison_anchor(repo, accepted_base, comparison_ref, anchor)
            if incorporated is not None:
                excluded.append(incorporated)
    if not excluded:
        raise ValueError("a contributor comparison history is required")
    result = subprocess.run(
        ["git", "log", "HEAD", "--not", *excluded, "--format=%aE", "--no-merges"],
        cwd=repo, capture_output=True, text=True, check=True, timeout=120,
    )
    return sorted(set(result.stdout.splitlines()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-ref")
    parser.add_argument("--accepted-base-file", type=Path)
    parser.add_argument("--official-tip", help="Trusted, separately fetched official upstream ref")
    args = parser.parse_args()
    accepted = (
        args.accepted_base_file.read_text(encoding="utf-8-sig").strip() if args.accepted_base_file else None
    )
    for email in new_author_emails(
        Path.cwd(), comparison_ref=args.comparison_ref, accepted_base=accepted, official_tip=args.official_tip
    ):
        print(email)


if __name__ == "__main__":
    main()
