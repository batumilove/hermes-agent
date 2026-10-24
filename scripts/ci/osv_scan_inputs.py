"""Prepare dependency scan inputs without treating the checkout as a PyPI release.

The root uv package is editable source, with a placeholder version. First-party
security is checked by the mandatory source guard regressions; registry packages
(including a same-named dependency) still go through the vulnerability scanner.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib


def _package_end(text: str, start: int, limit: int) -> int:
    """Keep unrelated trailing TOML sections outside the package span."""
    for header in re.finditer(r"(?m)^\[+([^\]\n]+)\]+[ \t]*(?:#.*)?$", text[start:limit]):
        name = header.group(1).strip()
        if name != "package" and not name.startswith("package."):
            return start + header.start()
    return limit


def project_root_lock(source: Path, destination: Path) -> None:
    """Remove exactly the editable checkout entry, preserving other lock bytes."""
    text = source.read_text(encoding="utf-8-sig")
    manifest = tomllib.loads((source.parent / "pyproject.toml").read_text(encoding="utf-8-sig"))
    project = manifest["project"]
    if project["name"] != "hermes-agent":
        raise ValueError("dependency projection requires the Hermes source checkout")
    matches = list(re.finditer(r"(?m)^\[\[package\]\]\s*$", text))
    removed = []
    for index, match in enumerate(matches):
        limit = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        end = _package_end(text, match.end(), limit)
        block = text[match.start():end]
        package = tomllib.loads(block)["package"][0]
        if (
            package.get("name") == project["name"]
            and package.get("version") == project["version"]
            and package.get("source") == {"editable": "."}
        ):
            removed.append((match.start(), end))
    if len(removed) != 1:
        raise ValueError("expected exactly one matching editable checkout package")
    start, end = removed[0]
    projected = text[:start] + text[end:]
    # Fail before publishing a malformed input. The original lock is never changed.
    tomllib.loads(projected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(projected, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.source.resolve() == args.destination.resolve():
        parser.error("dependency projection must not overwrite the source lock")
    project_root_lock(args.source, args.destination)


if __name__ == "__main__":
    main()
