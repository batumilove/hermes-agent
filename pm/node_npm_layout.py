"""Node is a runtime; npm comes exclusively from PM's independently pinned package."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile

from pm.filesystem import is_junction, native, remove_tree, retry_held
from pm.package import InstallError
from pm.store import extract

_NODE_RECEIPT = ".pm-node-runtime.json"
_NODE_POLICY = {"policy": "standalone-npm-v1"}


def _node_npm_paths(entry: Path, target: str) -> list[Path]:
    if target.startswith("win32"):
        return [entry / "node_modules/npm", *(entry / name for name in (
            "npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd", "npx.ps1"))]
    prefix = entry / "data/data/com.termux/files/usr" if target == "linux-arm64-bionic" else entry
    return [prefix / "lib/node_modules/npm", prefix / "bin/npm", prefix / "bin/npx"]


def strip_node_npm(entry: Path, target: str) -> None:
    """Strip only Node's redundant npm payload and launchers before PM hashes the tree."""
    for path in _node_npm_paths(entry, target):
        if path.is_symlink() or is_junction(path):
            retry_held(path.unlink if path.is_symlink() else path.rmdir)
        elif path.is_dir():
            retry_held(lambda: remove_tree(path))
        elif path.exists():
            retry_held(path.unlink)
    (entry / _NODE_RECEIPT).write_text(json.dumps(_NODE_POLICY) + "\n", encoding="utf-8")


def verify_node_npm(entry: Path, target: str) -> str:
    """Read-only rejection of pre-policy caches or reintroduced bundled npm."""
    try:
        if json.loads((entry / _NODE_RECEIPT).read_text(encoding="utf-8")) != _NODE_POLICY:
            return "Node standalone-npm staging receipt is unsupported"
    except (OSError, ValueError):
        return "Node standalone-npm staging receipt is missing or malformed"
    if any(path.exists() or path.is_symlink() or is_junction(path) for path in _node_npm_paths(entry, target)):
        return "Node still contains its redundant bundled npm"
    return ""


def install_selected_npm(archive: Path, staged: Path, node_binary: Path) -> None:
    """Bootstrap only from the already hash-verified npm artifact, offline without scripts."""
    from pm.packages import npm_env

    staged.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hermes-npm-bootstrap-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        extract(archive, root / "source")
        package = root / "source/package"
        try:
            metadata = json.loads((package / "package.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise InstallError("npm", "selected npm artifact has invalid metadata") from exc
        match = re.fullmatch(r"npm-(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)\.tgz", archive.name)
        cli = package / "bin/npm-cli.js"
        if (not isinstance(metadata, dict) or metadata.get("name") != "npm" or match is None
                or metadata.get("version") != match[1] or cli.is_symlink() or not cli.is_file()
                or (package / "package.json").is_symlink()):
            raise InstallError("npm", "selected npm artifact identity or CLI is unsupported")
        proc = subprocess.run(
            [native(node_binary), native(cli), "install", "--global", "--prefix", native(staged),
             "--offline", "--ignore-scripts", "--no-audit", "--no-fund", native(archive)],
            capture_output=True, text=True, timeout=900,
            env=npm_env(root / "cache"), check=False,
        )
    if proc.returncode != 0:
        raise InstallError("npm", f"self-install exited {proc.returncode}: {proc.stderr[-400:]}")
