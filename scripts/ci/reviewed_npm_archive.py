"""Verify exact reviewed npm source archives admitted to build contexts.

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


# Independent exact artifact and member pins; adjacent metadata cannot authorize bytes.
_COMPAT_PINS = {
    "vendor/npm/glob-7.2.3-hermes.1.tgz": (
        "804ada69dd8774e534af99b7d56701d427d119f13110c86110496c16d50f56d1",
        {
            "package/LICENSE": "6236fa0b88a4a0cce3dda0367979491b2052b3c8d6b1c10b3668de083e86a7f0",
            "package/README.md": "9edcb98721213c0293ac216337f3f97745add296879d9fd571152ec601f45b9e",
            "package/common.js": "b867e089ab5d4ab19a83e5b34da3dd7f4018fdf255fcacc681aab87d41dc77e8",
            "package/glob.js": "4caeca5af34b662a1598d6975bf6541a478443e6e79765095cf24479c3dbcaa4",
            "package/package.json": "c6b60d8e68b3aaa8e11fdd6ce01018ba9b558c77d5f59616c1392ec30321c9ef",
            "package/sync.js": "fea0ef6613ef0bcb7d103a37383cecb80f12797539e4c76604d57d5e15bce7a5",
        },
    ),
    "website/vendor/serve-handler-6.1.7-hermes.1.tgz": (
        "0868f9fa1dfa9ca59670387d936d8267dc3c7a1331174be7daa7cc153f4275e4",
        {
            "package/LICENSE": "be8ba42db3b7c3dbb064bcdc1bbbb191c0a39c8851c2a53564e97c7eea301bca",
            "package/README.md": "b5f53993835a62fb3c4a2e67aff1085c2f069d8cb536ee3d35684df1f27327cb",
            "package/package.json": "502fc5f71f868f123ba4e4853885d9201b14d2f14a8be786f16382757faccfd6",
            "package/src/directory.js": "efbf9f6b1effb357bc9df4424d66f46a37d70805ea474267704f78cd4bd248f0",
            "package/src/error.js": "8bc0f786bb342e1546f017867e79f5e51ef860dfdecf47d4e8eb26562ee478d1",
            "package/src/glob-slash.js": "f5cc5153d728efdf6167b23512263587cfecccaab035e0d48d075a9035d85c33",
            "package/src/index.js": "10b910fb2d6a0a380ab674e061dd0cb8273e888a69b105c2cc269492abae6582",
        },
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
            if (
                member.name not in expected
                or member.name in seen
                or not member.isfile()
            ):
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
    """Admit only exact reviewed artifacts, never a general vendor prefix."""
    try:
        compat = _COMPAT_PINS.get(relative_path.as_posix())
        if compat is not None:
            digest, inventory = compat
            data = _bounded_regular_bytes(root / relative_path, _MAX_ARCHIVE_BYTES)
            return hashlib.sha256(data).hexdigest() == digest and _matches_inventory(
                data, inventory
            )
        if relative_path != ARCHIVE_PATH:
            return False
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
