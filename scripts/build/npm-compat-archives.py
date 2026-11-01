#!/usr/bin/env python3
"""Rebuild the reviewed npm compatibility archives with deterministic tar metadata."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def build(name, version):
    source = ROOT / "vendor/npm" / name
    target = source.parent / f"{name}-{version}-hermes.1.tgz"
    custody = json.loads((source / "SOURCE.json").read_text())
    inventory = dict(custody["patched_sha256"])
    inventory["SOURCE.json"] = hashlib.sha256((source / "SOURCE.json").read_bytes()).hexdigest()
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, digest in sorted(inventory.items()):
                data = (source / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"Reviewed npm source differs: {name}")
                member = tarfile.TarInfo("package/" + name)
                member.size = len(data)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
    data = output.getvalue()
    target.write_bytes(data)
    target.with_suffix(".sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {target.name}\n")


if __name__ == "__main__":
    build("serve-handler", "6.1.7")
    build("glob", "7.2.3")
