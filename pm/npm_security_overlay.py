"""Apply npm's reviewed dependency security floors before PM publishes it.

Artifact URLs and SHA256 values are pinned together in PM's npm lock row. This
floor is an acceptance policy for the bundled dependency, not another lockfile.
Unsupported future layouts fail closed and require review of this overlay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re

from pm.filesystem import is_junction, remove_tree, retry_held
from pm.package import InstallError
from pm.store import extract

BRACE_EXPANSION_FLOOR = "5.0.12"
OVERLAY_URL = f"https://registry.npmjs.org/brace-expansion/-/brace-expansion-{BRACE_EXPANSION_FLOOR}.tgz"
_PAYLOAD = ".npm-security-overlay"
_RECEIPT = ".npm-security-overlay.json"
_REVIEWED_BUNDLES = {"5.0.9", "5.0.10", "5.0.11", "5.0.12"}
UNDICI_FLOOR = "6.29.0"
UNDICI_OVERLAY_URL = f"https://registry.npmjs.org/undici/-/undici-{UNDICI_FLOOR}.tgz"


@dataclass(frozen=True)
class _Overlay:
    name: str
    version: str
    url: str
    reviewed: frozenset[str]
    entrypoints: tuple[str, ...]

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    def receipt(self) -> dict[str, str]:
        return {"dependency": self.name, "version": self.version, "artifact_url": self.url}


_OVERLAYS = (
    _Overlay("brace-expansion", BRACE_EXPANSION_FLOOR, OVERLAY_URL, frozenset(_REVIEWED_BUNDLES),
             ("dist/commonjs/index.js", "dist/esm/index.js")),
    _Overlay("undici", UNDICI_FLOOR, UNDICI_OVERLAY_URL,
             frozenset({"6.27.0", "6.28.0", "6.28.1", "6.29.0"}),
             ("index.js", "lib/core/request.js")),
)


def _metadata(directory: Path, name: str, version: str | None = None) -> dict:
    if directory.is_symlink() or is_junction(directory) or (directory / "package.json").is_symlink():
        raise InstallError("npm", f"{name} package metadata must be a regular file")
    try:
        metadata = json.loads((directory / "package.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InstallError("npm", f"invalid {name} package metadata") from exc
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        raise InstallError("npm", f"unexpected package identity for {name}")
    if version is not None and metadata.get("version") != version:
        raise InstallError("npm", f"unexpected {name} version; expected {version}")
    return metadata


def _validate_payload(directory: Path, overlay: _Overlay) -> None:
    _metadata(directory, overlay.name, overlay.version)
    for relative in overlay.entrypoints:
        path = directory / relative
        if path.is_symlink() or not path.is_file():
            raise InstallError("npm", f"security overlay missing regular {relative}")


def unpack_overlay(archive: Path, staged: Path) -> bool:
    """Dispatch the extra locked archive without invoking npm or lifecycle scripts."""
    overlay = next((item for item in _OVERLAYS if archive.name == item.filename), None)
    if overlay is None:
        if re.fullmatch(r"npm-\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?\.tgz", archive.name):
            return False
        raise InstallError("npm", f"unsupported npm staging artifact {archive.name}")
    unpacked = staged / _PAYLOAD / overlay.name
    extract(archive, unpacked)
    entries = list(unpacked.iterdir())
    if len(entries) != 1 or entries[0].name != "package" or not entries[0].is_dir():
        raise InstallError("npm", "malformed security overlay tarball layout")
    _validate_payload(entries[0], overlay)
    return True


def _npm_root(staged: Path, target: str) -> Path:
    return staged / ("node_modules/npm" if target.startswith("win32") else "lib/node_modules/npm")


def apply_overlay(staged: Path, version: str, target: str) -> None:
    """Replace the reviewed dependency inside the unpublished npm tree."""
    root = _npm_root(staged, target)
    _metadata(root, "npm", version)
    for overlay in _OVERLAYS:
        _replace_dependency(staged, root, overlay)
    retry_held((staged / _PAYLOAD).rmdir)
    (staged / _RECEIPT).write_text(json.dumps({
        "npm_version": version, "overlays": [overlay.receipt() for overlay in _OVERLAYS],
    }, sort_keys=True) + "\n", encoding="utf-8")


def _replace_dependency(staged: Path, root: Path, overlay: _Overlay) -> None:
    incoming = staged / _PAYLOAD / overlay.name / "package"
    _validate_payload(incoming, overlay)
    destination = root / "node_modules" / overlay.name
    bundled = _metadata(destination, overlay.name)
    incoming_metadata = _metadata(incoming, overlay.name, overlay.version)
    if incoming_metadata.get("dependencies") != bundled.get("dependencies"):
        raise InstallError("npm", "security overlay changed dependency requirements; review the npm bundle")
    bundled_version = bundled.get("version")
    if not isinstance(bundled_version, str) or bundled_version not in overlay.reviewed:
        raise InstallError("npm", f"bundled {overlay.name} version is not reviewed for this overlay")
    if sorted(root.glob(f"node_modules/**/{overlay.name}/package.json")) != [destination / "package.json"]:
        raise InstallError("npm", f"unsupported nested {overlay.name} layout; review the npm overlay")
    retry_held(lambda: remove_tree(destination))
    retry_held(lambda: incoming.rename(destination))
    retry_held(incoming.parent.rmdir)


def verify_overlay(entry: Path, target: str) -> str:
    """Reject stale cache entries and missing/tampered overlay publication."""
    try:
        receipt = json.loads((entry / _RECEIPT).read_text(encoding="utf-8-sig"))
        if (not isinstance(receipt, dict) or not isinstance(receipt.get("npm_version"), str)
                or not receipt["npm_version"]
                or receipt.get("overlays") != [overlay.receipt() for overlay in _OVERLAYS]):
            return "npm security overlay receipt is missing or unsupported"
        root = _npm_root(entry, target)
        _metadata(root, "npm", receipt.get("npm_version"))
        for overlay in _OVERLAYS:
            _validate_payload(root / "node_modules" / overlay.name, overlay)
            if sorted(root.glob(f"node_modules/**/{overlay.name}/package.json")) != [root / "node_modules" / overlay.name / "package.json"]:
                return f"npm contains an unsupported nested {overlay.name} copy"
    except (OSError, ValueError, InstallError):
        return "npm security overlay is missing or malformed"
    return ""
