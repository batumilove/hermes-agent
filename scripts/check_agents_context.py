#!/usr/bin/env python3
"""Enforce the compact root AGENTS.md context contract."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

MAX_AGENTS_CHARS = 20_000
REFERENCE_RELATIVE_PATH = "docs/AGENTS_REFERENCE.md"
REFERENCE_LINK_LINE = (
    "Read [`docs/AGENTS_REFERENCE.md`](docs/AGENTS_REFERENCE.md) "
    "for the detailed version of this guide, including:"
)


def validate_contract(
    agents_path: Path,
    reference_path: Path,
    *,
    max_chars: int = MAX_AGENTS_CHARS,
) -> list[str]:
    """Return contract violations for the root guide and expanded reference."""
    errors: list[str] = []

    if not agents_path.is_file():
        errors.append(f"root agent guide is missing: {agents_path}")
        return errors

    agents = agents_path.read_text(encoding="utf-8")
    if len(agents) > max_chars:
        errors.append(
            f"{agents_path} has {len(agents)} characters; "
            f"exceeds {max_chars}-character contract"
        )

    if REFERENCE_LINK_LINE not in agents.splitlines():
        errors.append(
            f"{agents_path} must contain the canonical expanded-reference line"
        )

    if not reference_path.is_file():
        errors.append(f"expanded reference is missing: {reference_path}")

    return errors


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check that root AGENTS.md remains compact and contains its "
            "canonical expanded-reference line."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of scripts/)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = args.repo_root.resolve()
    errors = validate_contract(
        repo_root / "AGENTS.md",
        repo_root / REFERENCE_RELATIVE_PATH,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    agents_path = repo_root / "AGENTS.md"
    chars = len(agents_path.read_text(encoding="utf-8"))
    print(
        f"PASS: AGENTS.md context contract "
        f"({chars}/{MAX_AGENTS_CHARS} characters; canonical reference present)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
