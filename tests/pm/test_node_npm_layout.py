"""Node staging drops its redundant npm without changing runtime or header bytes."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from pm.install import _entry_current
from pm.node_npm_layout import strip_node_npm, verify_node_npm
from pm.registry import get_package
from pm.store import Store, current_target


def _layout(root, target):
    if target.startswith("win32"):
        return root, root / "node_modules/npm", [root / name for name in (
            "npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd", "npx.ps1")]
    prefix = root / "data/data/com.termux/files/usr" if target == "linux-arm64-bionic" else root
    return prefix, prefix / "lib/node_modules/npm", [prefix / "bin/npm", prefix / "bin/npx"]


@pytest.mark.parametrize("target", ["linux-x64", "darwin-arm64", "win32-x64", "linux-arm64-bionic"])
def test_node_staging_preserves_runtime_headers_and_unrelated_modules(tmp_path, target):
    prefix, npm, launchers = _layout(tmp_path, target)
    npm.mkdir(parents=True)
    (npm / "package.json").write_text('{"name":"npm","version":"11.19.0"}', encoding="utf-8")
    for launcher in launchers:
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_bytes(b"redundant npm launcher")
    runtime = prefix / ("node.exe" if target.startswith("win32") else "bin/node")
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"runtime identity")
    header = prefix / "include/node/node.h"
    header.parent.mkdir(parents=True)
    header.write_bytes(b"native addon headers")
    neighbour = npm.parent / "unrelated"
    neighbour.mkdir()
    (neighbour / "package.json").write_bytes(b"independent module")
    get_package("node").stage(None, tmp_path, "26.7.0", target)
    assert not npm.exists()
    assert not any(launcher.exists() for launcher in launchers)
    assert runtime.read_bytes() == b"runtime identity"
    assert header.read_bytes() == b"native addon headers"
    assert (neighbour / "package.json").read_bytes() == b"independent module"
    assert verify_node_npm(tmp_path, target) == ""


def test_node_verifier_rejects_old_cache_and_reintroduced_npm_without_writes(tmp_path):
    entry = tmp_path / "node"
    entry.mkdir()
    assert verify_node_npm(entry, "linux-x64")
    assert not list(entry.iterdir())
    strip_node_npm(entry, "linux-x64")
    vendor = entry / "lib/node_modules/npm"
    vendor.mkdir(parents=True)
    metadata = vendor / "package.json"
    metadata.write_text(json.dumps({"name": "npm", "version": "11.19.0"}), encoding="utf-8")
    before = metadata.read_bytes()
    assert verify_node_npm(entry, "linux-x64")
    assert metadata.read_bytes() == before


@pytest.mark.platforms("posix")
def test_node_stale_stage_is_rejected_then_native_runtime_still_executes(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("native Node is required for the runtime consumer")
    target = current_target()
    store = Store(tmp_path / "store")
    entry = store.entry("node-runtime")
    binary = get_package("node").binary(entry, target)
    binary.parent.mkdir(parents=True)
    binary.symlink_to(Path(node).resolve())
    pin = '{"target":"native","sha256":[]}'
    (entry / ".pm-stage-pin.json").write_text(pin, encoding="utf-8")
    assert not _entry_current(get_package("node"), None, None, store, entry, None, "26.7.0", pin, target)
    get_package("node").stage(store, entry, "26.7.0", target)
    assert _entry_current(get_package("node"), None, None, store, entry, None, "26.7.0", pin, target)
    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0 and result.stdout.startswith("v"), result.stderr
