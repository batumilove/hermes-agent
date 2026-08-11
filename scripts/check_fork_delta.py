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


@dataclass(frozen=True)
class UpstreamRelationship:
    base: str
    contributor_base: str
    mode: str


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


def _is_ancestor(ancestor: str, descendant: str, *, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = (result.stderr or result.stdout).strip().splitlines()
    raise DeltaError(
        detail[0]
        if detail
        else f"git merge-base --is-ancestor {ancestor} {descendant} failed"
    )


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeltaError(f"{field} must be a non-empty string")
    return value.strip()


def _repo_path(value: Any, field: str) -> Path:
    relative = _nonempty_string(value, field)
    candidate = (ROOT / relative).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DeltaError(f"{field} must stay inside the repository") from exc
    return candidate


def load_manifest(path: Path) -> tuple[Path, Path | None, list[PatchOwner]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DeltaError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise DeltaError("manifest version must be 1")

    upstream = raw.get("upstream")
    if not isinstance(upstream, dict):
        raise DeltaError("manifest upstream section must be a mapping")
    base_file = _repo_path(upstream.get("base_file"), "upstream.base_file")
    provenance_value = upstream.get("provenance_file")
    provenance_file = (
        _repo_path(provenance_value, "upstream.provenance_file")
        if provenance_value is not None
        else None
    )

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
    return base_file, provenance_file, owners


def _read_sha_file(path: Path, field: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DeltaError(f"could not read {field} {path}: {exc}") from exc
    if not _SHA_RE.fullmatch(value):
        raise DeltaError(f"{field} must contain one full lowercase SHA")
    return value


def _load_provenance(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeltaError(f"could not read squash-sync provenance {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise DeltaError("squash-sync provenance version must be 1")
    expected = {
        "version",
        "upstream_base",
        "source_head",
        "squash_commit",
        "fetch_depth",
    }
    if set(raw) != expected:
        raise DeltaError(f"squash-sync provenance fields must be {sorted(expected)}")
    values: dict[str, Any] = {}
    for field in ("upstream_base", "source_head", "squash_commit"):
        value = raw.get(field)
        if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
            raise DeltaError(f"squash-sync provenance {field} must be a full lowercase SHA")
        values[field] = value
    fetch_depth = raw.get("fetch_depth")
    if (
        isinstance(fetch_depth, bool)
        or not isinstance(fetch_depth, int)
        or not 1 <= fetch_depth <= 1024
    ):
        raise DeltaError("squash-sync provenance fetch_depth must be an integer from 1 to 1024")
    values["fetch_depth"] = fetch_depth
    return values


def resolve_upstream_relationship(
    manifest_path: Path,
    *,
    head: str = "HEAD",
    cwd: Path = ROOT,
) -> UpstreamRelationship:
    base_file, provenance_file, _ = load_manifest(manifest_path)
    base = _read_sha_file(base_file, str(base_file.relative_to(ROOT)))
    resolved_head = _git("rev-parse", head, cwd=cwd)
    _git("cat-file", "-e", f"{base}^{{commit}}", cwd=cwd)
    if _is_ancestor(base, resolved_head, cwd=cwd):
        return UpstreamRelationship(base, base, "direct")
    if provenance_file is None:
        raise DeltaError("recorded upstream base is not an ancestor and no provenance file is configured")

    provenance = _load_provenance(provenance_file)
    if provenance["upstream_base"] != base:
        raise DeltaError("squash-sync provenance upstream base does not match the recorded base")
    source = provenance["source_head"]
    squash = provenance["squash_commit"]
    _git("cat-file", "-e", f"{source}^{{commit}}", cwd=cwd)
    _git("cat-file", "-e", f"{squash}^{{commit}}", cwd=cwd)
    if not _is_ancestor(base, source, cwd=cwd):
        raise DeltaError("upstream base is not an ancestor of the recorded sync source")
    if not _is_ancestor(squash, resolved_head, cwd=cwd):
        raise DeltaError("recorded squash commit is not an ancestor of HEAD")

    source_tree = _git("rev-parse", f"{source}^{{tree}}", cwd=cwd)
    squash_tree = _git("rev-parse", f"{squash}^{{tree}}", cwd=cwd)
    if source_tree != squash_tree:
        raise DeltaError("recorded sync source and squash trees differ")
    base_relative = base_file.relative_to(ROOT).as_posix()
    source_base = _git("show", f"{source}:{base_relative}", cwd=cwd).strip()
    if source_base != base:
        raise DeltaError("recorded sync source does not contain the recorded upstream base")
    return UpstreamRelationship(base, squash, "squash-sync")


def resolve_contributor_base(
    manifest_path: Path,
    *,
    head: str = "HEAD",
    cwd: Path = ROOT,
) -> str:
    return resolve_upstream_relationship(manifest_path, head=head, cwd=cwd).contributor_base


def fetch_provenance_source(manifest_path: Path, *, cwd: Path = ROOT) -> str | None:
    base_file, provenance_file, _ = load_manifest(manifest_path)
    if provenance_file is None:
        return None
    base = _read_sha_file(base_file, str(base_file.relative_to(ROOT)))
    resolved_head = _git("rev-parse", "HEAD", cwd=cwd)
    try:
        _git("cat-file", "-e", f"{base}^{{commit}}", cwd=cwd)
    except DeltaError:
        pass
    else:
        if _is_ancestor(base, resolved_head, cwd=cwd):
            return None
    provenance = _load_provenance(provenance_file)
    source = provenance["source_head"]
    try:
        _git("cat-file", "-e", f"{source}^{{commit}}", cwd=cwd)
        if _is_ancestor(base, source, cwd=cwd):
            return source
    except DeltaError:
        pass
    _git(
        "fetch",
        f"--depth={provenance['fetch_depth']}",
        "--filter=blob:none",
        "--no-tags",
        "origin",
        source,
        cwd=cwd,
    )
    return source


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
    _, _, owners = load_manifest(manifest_path)
    relationship = resolve_upstream_relationship(manifest_path, head=head, cwd=cwd)
    base = relationship.base
    resolved_head = _git("rev-parse", head, cwd=cwd)
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
        "contributor_base": relationship.contributor_base,
        "provenance": relationship.mode,
        "changed_count": len(changed),
        "unexplained": classified.pop("unexplained"),
        "patches": {key: value for key, value in classified.items() if value},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--json", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--contributor-base", action="store_true")
    action.add_argument("--fetch-provenance-source", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.fetch_provenance_source:
            source = fetch_provenance_source(args.manifest.resolve())
            if source:
                print(source)
            return 0
        if args.contributor_base:
            print(
                resolve_contributor_base(
                    args.manifest.resolve(),
                    head=args.head,
                )
            )
            return 0
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
