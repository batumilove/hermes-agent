"""Verify the reviewed npm source archives admitted to build contexts.

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
_MAX_ARCHIVE_BYTES = 65536
_MAX_MEMBER_BYTES = 32768
_MAX_CONTENT_BYTES = 65536

_REVIEWED_ARCHIVES = {
    ARCHIVE_PATH: (_ARCHIVE_SHA256, _SOURCE_SHA256, _SOURCE_PATH),
    Path("vendor/npm/glob-7.2.3-hermes.1.tgz"): (
        "69c46f24be0a84b4369bc8c3388b866a08e4d1202c971676f0bc17dac3407f58",
        "5dae56a583849f114e309c2132d4f021c388e7aa564e9ace8faaf87a5a4ca923",
        Path("vendor/npm/glob/SOURCE.json"),
    ),
    Path("vendor/npm/serve-handler-6.1.7-hermes.1.tgz"): (
        "5c12db8eb698d6168de774211caca57faad0a62a3e08438fb724a0d4ff6ec222",
        "5337fed91f0d6f55ecd91232e4ffe54cf643c649bf6a4fcf26bffdffe1a91b6b",
        Path("vendor/npm/serve-handler/SOURCE.json"),
    ),
}


def _bounded_regular_bytes(path: Path, limit: int) -> bytes:
    # Ancestor links are also forbidden: a checkout cannot redirect the proof.
    if path.resolve() != path.absolute() or not path.is_file():
        raise ValueError("reviewed build input must be a regular, unlinked file")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("reviewed build input exceeds its size bound")
    return data


def _expected_inventory(source_data: bytes, source_digest: str = _SOURCE_SHA256) -> dict[str, str]:
    if hashlib.sha256(source_data).hexdigest() != source_digest:
        raise ValueError("unreviewed source provenance")
    source = json.loads(source_data)
    inventory = {
        f"package/{name}": digest for name, digest in source["patched_sha256"].items()
    }
    inventory["package/SOURCE.json"] = source_digest
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
    """Admit exact reviewed artifacts, never a general vendor prefix."""
    pin = _REVIEWED_ARCHIVES.get(relative_path)
    if pin is None:
        return False
    archive_digest, source_digest, source_path = pin
    try:
        archive_data = _bounded_regular_bytes(root / relative_path, _MAX_ARCHIVE_BYTES)
        if hashlib.sha256(archive_data).hexdigest() != archive_digest:
            return False
        sidecar = _bounded_regular_bytes(root / relative_path.with_suffix(".sha256"), 256)
        if sidecar != f"{archive_digest}  {relative_path.name}\n".encode("ascii"):
            return False
        source = _bounded_regular_bytes(root / source_path, 16384)
        return _matches_inventory(archive_data, _expected_inventory(source, source_digest))
    except (OSError, ValueError, tarfile.TarError, EOFError):
        # Invalid proof is an ordinary forbidden archive; never log its bytes.
        return False
