"""Pinned upstream release identity for source trees imported by squash."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

IMPORTED_RELEASE = (
    "0.22.5", "56b6a31fededdf7364e1a0a79548d144e7fd81a3",
    "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
)


def imported_release_version(repo: Path) -> tuple[str, int] | None:
    """Admit a pinned release only with its tag object and complete local proof."""
    if not (repo / ".github/upstream-sync-provenance.json").is_file():
        return None
    from hermes_cli import fork_provenance as provenance

    version, tag_object, release = IMPORTED_RELEASE
    try:
        with provenance.offline_git():
            if provenance.git(repo, "cat-file", "-t", tag_object) != "tag":
                return None
            if provenance.git(repo, "rev-parse", f"{tag_object}^{{commit}}") != release:
                return None
            tag = provenance.git(repo, "cat-file", "tag", tag_object)
            header = tag.split("\n\n", 1)[0].splitlines()
            if f"tag v{version}" not in header:
                return None
            head = provenance.git(repo, "rev-parse", "HEAD")
            return version, provenance.source_distance(repo, release, head)
    except (provenance.ProvenanceError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
