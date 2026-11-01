#!/usr/bin/env python3
"""Reproduce the serve-handler adapter for minimatch's named CommonJS export."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

SOURCE = "https://registry.npmjs.org/serve-handler/-/serve-handler-6.1.7.tgz"


def build(source: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as original:
        with gzip.GzipFile(
            fileobj=output, mode="wb", filename="", mtime=0
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as result:
                for member in sorted(original.getmembers(), key=lambda item: item.name):
                    if ".." in Path(member.name).parts or not member.name.startswith(
                        "package/"
                    ):
                        raise ValueError("unexpected upstream archive path")
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ValueError("unexpected upstream archive member type")
                    data = original.extractfile(member).read()
                    if member.name == "package/src/index.js":
                        before = b"const minimatch = require('minimatch');"
                        if data.count(before) != 1:
                            raise ValueError("upstream minimatch call site changed")
                        data = data.replace(
                            before, b"const {minimatch} = require('minimatch');"
                        )
                    if member.name == "package/package.json":
                        package = json.loads(data)
                        package["version"] = "6.1.7-hermes.1"
                        package["dependencies"]["minimatch"] = "11.0.1"
                        data = (json.dumps(package, indent=2) + "\n").encode()
                    info = tarfile.TarInfo(member.name)
                    info.size, info.mode, info.mtime = len(data), member.mode, 0
                    result.addfile(info, io.BytesIO(data))
    return output.getvalue()


def main() -> None:
    folder = Path(__file__).resolve().parent.parent / "vendor"
    metadata = json.loads((folder / "serve-handler-compat.json").read_text())
    with urllib.request.urlopen(SOURCE, timeout=30) as response:
        source = response.read()
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(source).digest()).decode()
    if integrity != metadata["source_integrity"]:
        raise ValueError("upstream archive integrity mismatch")
    artifact = build(source)
    if hashlib.sha256(artifact).hexdigest() != metadata["output_sha256"]:
        raise ValueError("compatibility artifact hash mismatch")
    target = folder / "serve-handler-6.1.7-hermes.1.tgz"
    target.write_bytes(artifact)
    print(hashlib.sha256(artifact).hexdigest(), target.name)


if __name__ == "__main__":
    main()
