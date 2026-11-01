#!/usr/bin/env python3
"""Reproduce the glob 7 adapter for minimatch 11's nonrecursive Matcher API."""

import base64, gzip, hashlib, io, json, tarfile, urllib.request
from pathlib import Path


def patch_member(name, data):
    if name in ("package/glob.js", "package/sync.js"):
        for before, after, count in [
            (b"pn._glob", b"pn.pattern", 1),
            (b"e.match(pn)", b"pn.test(e)", 2),
        ]:
            if data.count(before) != count:
                raise ValueError("upstream matcher call sites changed")
            data = data.replace(before, after)
    if name == "package/package.json":
        package = json.loads(data)
        package["version"] = "7.2.3-hermes.1"
        package["dependencies"]["minimatch"] = "11.0.1"
        data = (json.dumps(package, indent=2) + "\n").encode()
    return data


def build(source):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as original:
        with gzip.GzipFile(
            fileobj=output, mode="wb", filename="", mtime=0
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as result:
                for member in sorted(original.getmembers(), key=lambda x: x.name):
                    if ".." in Path(member.name).parts or not member.name.startswith(
                        "package/"
                    ):
                        raise ValueError("unexpected upstream path")
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ValueError("unexpected member type")
                    data = original.extractfile(member).read()
                    data = patch_member(member.name, data)
                    info = tarfile.TarInfo(member.name)
                    info.size = len(data)
                    info.mode = member.mode
                    info.mtime = 0
                    result.addfile(info, io.BytesIO(data))
    return output.getvalue()


def main():
    folder = Path(__file__).resolve().parent.parent / "vendor/npm"
    metadata = json.loads((folder / "glob-compat.json").read_text(encoding="utf-8-sig"))
    source = urllib.request.urlopen(metadata["source_url"], timeout=30).read()
    if (
        "sha512-" + base64.b64encode(hashlib.sha512(source).digest()).decode()
        != metadata["source_integrity"]
    ):
        raise ValueError("source integrity mismatch")
    artifact = build(source)
    if hashlib.sha256(artifact).hexdigest() != metadata["output_sha256"]:
        raise ValueError("output hash mismatch")
    (folder / "glob-7.2.3-hermes.1.tgz").write_bytes(artifact)
    print(metadata["output_sha256"])


if __name__ == "__main__":
    main()
