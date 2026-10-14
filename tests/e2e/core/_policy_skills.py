"""Authentic commit-pinned worker policies for isolated real-process E2E homes."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/kanban_policy_skills"
_SOURCE_SHA256 = "16183d2924939a033aa661de47e86eb7789fe9509bd70f941d991c5a6b750c2c"
_ARCHIVE_SHA256 = "0a2059fb4cd70eb4763364c0cb30a5f11e164a5d00ab9314c2f74dee0152dc3d"


def _verified_members(fixture: Path) -> dict[str, bytes]:
    """Verify the source receipt, archive and every original Git blob before writing."""
    source_data = (fixture / "SOURCE.json").read_bytes()
    archive_data = (fixture / "skills.zip").read_bytes()
    if hashlib.sha256(source_data).hexdigest() != _SOURCE_SHA256:
        raise ValueError("worker policy source receipt differs from the reviewed pin")
    if hashlib.sha256(archive_data).hexdigest() != _ARCHIVE_SHA256:
        raise ValueError("worker policy archive differs from the reviewed pin")
    source = json.loads(source_data)
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(source["files"]):
            raise ValueError("worker policy archive does not match its source inventory")
        for name, proof in source["files"].items():
            data = archive.read(name)
            blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
            if (len(data) != proof["bytes"]
                    or hashlib.sha256(data).hexdigest() != proof["sha256"]
                    or hashlib.sha1(blob).hexdigest() != proof["git_blob"]):
                raise ValueError(f"worker policy asset differs from its source blob: {name}")
            members[name] = data
    return members


def provision_policy_skills(hermes_home: Path, *, fixture: Path = FIXTURE) -> None:
    """Populate only the test home's skill inventory; no services or setup scripts run."""
    members = _verified_members(fixture)
    for name, data in members.items():
        target = hermes_home / "skills" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
