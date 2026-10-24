"""Verify the single reviewed npm source archive admitted to build contexts.

Pins here are independent of the adjacent sidecar and provenance manifest.
Changing either cannot authorize an arbitrary archive or a profile export.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile

ARCHIVE_PATH = Path("vendor/npm/braces-3.0.3-hermes.1.tgz")
_ARCHIVE_SHA256 = "a59528943e71f9a778b01a88ffec2f7a3cb8ebe2aac1854430f9c9a7df02d7bb"
_SOURCE_SHA256 = "14063bd6d17b7e43fef27514ba74482637de98201b16415e1da5d6d7e8405883"
_SOURCE_PATH = Path("vendor/npm/braces/SOURCE.json")
_SIDECAR_PATH = Path("vendor/npm/braces-3.0.3-hermes.1.sha256")
_MAX_ARCHIVE_BYTES = 65536
_MAX_MEMBER_BYTES = 32768
_MAX_CONTENT_BYTES = 65536
_UPSTREAM_IDENTITY = {
    "package": "braces",
    "version": "3.0.3",
    "repository": "https://github.com/micromatch/braces",
    "commit": "74b2db2938fad48a2ea54a9c8bf27a37a62c350d",
    "tarball": "https://registry.npmjs.org/braces/-/braces-3.0.3.tgz",
    "integrity": "sha512-yQbXgO/OSZVD2IsiLlro+7Hf6Q18EJrKSEsdoMzKePKXct3gvD8oLcOQdIzGupr5Fj+EDe8gO/lxc1BzfMpxvA==",
    "tarball_sha256": "1cd18e862c8640b4568b1425a7df4ee030ff201d45b2da8f9f222d2987494ffc",
}
_FORK_IDENTITY = {"name": "@hermes-security/braces", "version": "3.0.3-hermes.1"}


def _bounded_regular_bytes(path: Path, limit: int) -> bytes:
    # Ancestor links are also forbidden: a checkout cannot redirect the proof.
    if path.resolve() != path.absolute() or not path.is_file():
        raise ValueError("reviewed build input must be a regular, unlinked file")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("reviewed build input exceeds its size bound")
    return data


def _expected_inventory(source_data: bytes) -> dict[str, str]:
    if hashlib.sha256(source_data).hexdigest() != _SOURCE_SHA256:
        raise ValueError("unreviewed source provenance")
    source = json.loads(source_data)
    if source["upstream"] != _UPSTREAM_IDENTITY or source["fork"] != _FORK_IDENTITY:
        raise ValueError("source identity does not match the reviewed pin")
    inventory = {
        f"package/{name}": digest for name, digest in source["patched_sha256"].items()
    }
    inventory["package/SOURCE.json"] = _SOURCE_SHA256
    return inventory


def _matches_inventory(archive_data: bytes, expected: dict[str, str]) -> bool:
    """Check every member without extraction, following links or executing code."""
    seen: set[str] = set()
    content_size = 0
    with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r|gz") as archive:
        for member in archive:
            if member.name not in expected or member.name in seen or not member.isfile():
                return False
            if member.size < 0 or member.size > _MAX_MEMBER_BYTES:
                return False
            content_size += member.size
            if content_size > _MAX_CONTENT_BYTES:
                return False
            payload = archive.extractfile(member)
            if payload is None:
                return False
            with payload:
                data = payload.read(_MAX_MEMBER_BYTES + 1)
            if len(data) != member.size:
                return False
            if hashlib.sha256(data).hexdigest() != expected[member.name]:
                return False
            seen.add(member.name)
    return seen == expected.keys()


def is_reviewed_npm_build_input(root: Path, relative_path: Path) -> bool:
    """Admit only this exact reviewed artifact, never a general vendor prefix."""
    if relative_path != ARCHIVE_PATH:
        return False
    try:
        archive_data = _bounded_regular_bytes(root / relative_path, _MAX_ARCHIVE_BYTES)
        if hashlib.sha256(archive_data).hexdigest() != _ARCHIVE_SHA256:
            return False
        sidecar = _bounded_regular_bytes(root / _SIDECAR_PATH, 256)
        if sidecar != f"{_ARCHIVE_SHA256}  {ARCHIVE_PATH.name}\n".encode("ascii"):
            return False
        source = _bounded_regular_bytes(root / _SOURCE_PATH, 16384)
        return _matches_inventory(archive_data, _expected_inventory(source))
    except (OSError, ValueError, tarfile.TarError, EOFError):
        # Invalid proof is an ordinary forbidden archive; never log its bytes.
        return False
