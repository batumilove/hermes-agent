#!/usr/bin/env python3
"""Require every fork-to-upstream path delta to have an explicit owner."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".github" / "batumi-patches.yaml"
VALID_KINDS = {
    "cleanup",
    "compatibility",
    "core",
    "extension",
    "operations",
    "skill",
}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class DeltaError(RuntimeError):
    """Raised when the manifest or repository relationship is invalid."""


@dataclass(frozen=True)
class PatchOwner:
    patch_id: str
    kind: str
    paths: tuple[str, ...]


def _git(*args: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise DeltaError(detail[0] if detail else f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeltaError(f"{field} must be a non-empty string")
    return value.strip()


def load_manifest(path: Path) -> tuple[Path, list[PatchOwner]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DeltaError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise DeltaError("manifest version must be 1")

    upstream = raw.get("upstream")
    if not isinstance(upstream, dict):
        raise DeltaError("manifest upstream section must be a mapping")
    base_file_value = _nonempty_string(upstream.get("base_file"), "upstream.base_file")
    base_file = (ROOT / base_file_value).resolve()
    try:
        base_file.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DeltaError("upstream.base_file must stay inside the repository") from exc

    patches = raw.get("patches")
    if not isinstance(patches, list) or not patches:
        raise DeltaError("manifest patches must be a non-empty list")

    owners: list[PatchOwner] = []
    seen_ids: set[str] = set()
    for index, patch in enumerate(patches):
        prefix = f"patches[{index}]"
        if not isinstance(patch, dict):
            raise DeltaError(f"{prefix} must be a mapping")
        patch_id = _nonempty_string(patch.get("id"), f"{prefix}.id")
        if patch_id in seen_ids:
            raise DeltaError(f"duplicate patch id: {patch_id}")
        seen_ids.add(patch_id)
        kind = _nonempty_string(patch.get("kind"), f"{prefix}.kind")
        if kind not in VALID_KINDS:
            raise DeltaError(f"{prefix}.kind must be one of {sorted(VALID_KINDS)}")
        _nonempty_string(patch.get("rationale"), f"{prefix}.rationale")
        _nonempty_string(patch.get("retirement"), f"{prefix}.retirement")
        tests = patch.get("tests")
        if not isinstance(tests, list) or not all(
            isinstance(test, str) and test.strip() for test in tests
        ):
            raise DeltaError(f"{prefix}.tests must be a non-empty string list")
        paths = patch.get("paths")
        if not isinstance(paths, list) or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in paths
        ):
            raise DeltaError(f"{prefix}.paths must be a non-empty string list")
        owners.append(PatchOwner(patch_id, kind, tuple(paths)))
    return base_file, owners


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**") and path == pattern[:-3]:
        return True
    return fnmatch.fnmatchcase(path, pattern)


def classify_paths(paths: list[str], owners: list[PatchOwner]) -> dict[str, list[str]]:
    classified: dict[str, list[str]] = {owner.patch_id: [] for owner in owners}
    unexplained: list[str] = []
    for path in paths:
        matches = [
            owner.patch_id
            for owner in owners
            if any(_matches(path, pattern) for pattern in owner.paths)
        ]
        if not matches:
            unexplained.append(path)
            continue
        for patch_id in matches:
            classified[patch_id].append(path)
    classified["unexplained"] = unexplained
    return classified


def check_delta(
    manifest_path: Path,
    *,
    head: str = "HEAD",
    cwd: Path = ROOT,
) -> dict[str, Any]:
    base_file, owners = load_manifest(manifest_path)
    try:
        base = base_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DeltaError(f"could not read upstream base file {base_file}: {exc}") from exc
    if not _SHA_RE.fullmatch(base):
        raise DeltaError(f"{base_file.relative_to(ROOT)} must contain one full lowercase SHA")

    resolved_head = _git("rev-parse", head, cwd=cwd)
    _git("cat-file", "-e", f"{base}^{{commit}}", cwd=cwd)
    _git("merge-base", "--is-ancestor", base, resolved_head, cwd=cwd)
    changed = _git(
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        f"{base}..{resolved_head}",
        cwd=cwd,
    ).splitlines()
    classified = classify_paths(changed, owners)
    return {
        "base": base,
        "head": resolved_head,
        "changed_count": len(changed),
        "unexplained": classified.pop("unexplained"),
        "patches": {key: value for key, value in classified.items() if value},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = check_delta(args.manifest.resolve(), head=args.head)
    except DeltaError as exc:
        print(f"fork delta check failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"Fork delta: {report['changed_count']} paths from "
            f"{report['base'][:12]} to {report['head'][:12]}"
        )
        for patch_id, paths in sorted(report["patches"].items()):
            print(f"  {patch_id}: {len(paths)}")
        if report["unexplained"]:
            print("Unexplained paths:", file=sys.stderr)
            for path in report["unexplained"]:
                print(f"  {path}", file=sys.stderr)

    return 1 if report["unexplained"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
