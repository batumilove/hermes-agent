"""PM publishes the patched npm dependency as one verified artifact transaction."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from pm.install import _install
from pm.lock import Lockfile
from pm.npm_security_overlay import BRACE_EXPANSION_FLOOR, OVERLAY_URL
from pm.package import InstallError
from pm.registry import get_package
from pm.store import Store, extract, tree_digest

_NPM_VERSION = "12.1.0"
_DEPENDENCIES = {"balanced-match": "^4.0.2"}


def _tar(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, value in files.items():
            content = (json.dumps(value) if isinstance(value, dict) else value).encode()
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(content), 0o644
            archive.addfile(member, io.BytesIO(content))
    return data.getvalue()


def _bundle_files(version="5.0.9"):
    return {
        "package/package.json": {"name": "npm", "version": _NPM_VERSION},
        "package/node_modules/brace-expansion/package.json": {
            "name": "brace-expansion", "version": version, "dependencies": _DEPENDENCIES,
        },
        "package/node_modules/brace-expansion/old-bundled.js": "old dependency",
        "package/bin/npm-cli.js": "npm fixture",
        "package/bin/npx-cli.js": "npx fixture",
    }


def _overlay_files():
    return {
        "package/package.json": {"name": "brace-expansion", "version": BRACE_EXPANSION_FLOOR,
                                 "dependencies": _DEPENDENCIES,
                                 "scripts": {"postinstall": "must never run"}},
        "package/dist/commonjs/index.js": "exports.expand = s => [s];",
        "package/dist/esm/index.js": "export const expand = s => [s];",
    }


def _pin(store, lock, files, overlay=None, *, include_overlay=True, overlay_url=OVERLAY_URL):
    archives = [(f"https://registry.npmjs.org/npm/-/npm-{_NPM_VERSION}.tgz", _tar(files))]
    if include_overlay:
        archives.append((overlay_url, _tar(_overlay_files() if overlay is None else overlay)))
    artifacts = []
    for url, payload in archives:
        digest = hashlib.sha256(payload).hexdigest()
        cached = store.entry(f"fetch-{digest}")
        cached.mkdir(parents=True, exist_ok=True)
        (cached / url.rsplit("/", 1)[-1]).write_bytes(payload)
        artifacts.append({"url": url, "sha256": digest})
    lock.set_pin("npm", _NPM_VERSION, {"any": artifacts})
    lock.save()


@pytest.fixture
def stage_setup(tmp_path, monkeypatch):
    store = Store(tmp_path / "store")
    lock = Lockfile(tmp_path / "lock.json")
    calls = []
    selection = {}

    def installed_node(_package, _lock, target):
        node = get_package("node").binary(store.entry("node-fixture"), target)
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_bytes(b"node fixture")
        lib = node.parent if target.startswith("win32") else node.parent.parent / "lib"
        cli = lib / "node_modules/npm/bin/npm-cli.js"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("bundled cli fixture", encoding="utf-8")
        return {"node": {"entry": "node-fixture"}}, store

    def self_install(cmd, **kwargs):
        assert "--offline" in cmd and "--ignore-scripts" in cmd
        calls.append(cmd)
        destination = Path(cmd[cmd.index("--prefix") + 1])
        unpacked = destination / ".fixture-install"
        extract(Path(cmd[-1]), unpacked)
        target = selection["target"]
        root = destination / ("node_modules/npm" if target.startswith("win32") else "lib/node_modules/npm")
        root.parent.mkdir(parents=True, exist_ok=True)
        (unpacked / "package").rename(root)
        unpacked.rmdir()
        binary = get_package("npm").binary(destination, target)
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("npm shim fixture", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("pm.install._installed_location", installed_node)
    monkeypatch.setattr("pm.packages.subprocess.run", self_install)
    monkeypatch.setattr("pm.install._lockfile", lambda: lock)
    return store, lock, calls, selection


def _publish(setup, target):
    store, lock, _calls, selection = setup
    selection["target"] = target
    return _install(get_package("npm"), lock, None, store, target)


@pytest.mark.parametrize("target", ["linux-x64", "win32-x64", "linux-arm64-bionic"])
def test_security_overlay_is_inside_published_npm_consumer_tree(stage_setup, target):
    store, lock, calls, _selection = stage_setup
    _pin(store, lock, _bundle_files())
    entry = _publish(stage_setup, target)
    root = entry / ("node_modules/npm" if target.startswith("win32") else "lib/node_modules/npm")
    dependency = root / "node_modules/brace-expansion"
    assert json.loads((dependency / "package.json").read_text(encoding="utf-8-sig"))["version"] == BRACE_EXPANSION_FLOOR
    assert (dependency / "dist/commonjs/index.js").is_file()
    assert not (dependency / "old-bundled.js").exists()
    assert not (entry / ".npm-security-overlay").exists()
    assert get_package("npm").verify(entry, target) == ""
    assert len(calls) == (0 if target == "linux-arm64-bionic" else 1)


@pytest.mark.parametrize("problem", ["missing", "wrong-version", "wrong-identity", "missing-entrypoint", "nested", "future-major", "malformed-version", "dependency-change", "wrong-artifact"])
def test_bad_or_unsupported_overlay_cannot_replace_published_npm(stage_setup, problem):
    store, lock, _calls, _selection = stage_setup
    _pin(store, lock, _bundle_files())
    entry = _publish(stage_setup, "linux-x64")
    before = tree_digest(entry)
    bundle, overlay = _bundle_files(), _overlay_files()
    overlay_metadata = overlay["package/package.json"]
    bundled_metadata = bundle["package/node_modules/brace-expansion/package.json"]
    edits = {
        "wrong-version": (overlay, "package/package.json", {**overlay_metadata, "version": "5.0.11"}),
        "wrong-identity": (overlay, "package/package.json", {**overlay_metadata, "name": "unexpected-package"}),
        "missing-entrypoint": (overlay, "package/dist/commonjs/index.js", None),
        "nested": (bundle, "package/node_modules/nested/node_modules/brace-expansion/package.json", bundled_metadata),
        "future-major": (bundle, "package/node_modules/brace-expansion/package.json", {**bundled_metadata, "version": "6.0.0"}),
        "malformed-version": (bundle, "package/node_modules/brace-expansion/package.json", {**bundled_metadata, "version": ["5.0.9"]}),
        "dependency-change": (overlay, "package/package.json", {**overlay_metadata, "dependencies": {"balanced-match": "^5.0.0"}}),
        "wrong-artifact": (overlay, "package/unsupported-artifact.txt", "unreviewed artifact"),
    }
    if problem in edits:
        files, path, value = edits[problem]
        if value is None:
            files.pop(path)
        else:
            files[path] = value
    overlay_url = OVERLAY_URL.replace("5.0.12", "5.0.13") if problem == "wrong-artifact" else OVERLAY_URL
    _pin(store, lock, bundle, overlay, include_overlay=problem != "missing", overlay_url=overlay_url)
    with pytest.raises(InstallError):
        _publish(stage_setup, "linux-x64")
    assert tree_digest(entry) == before


@pytest.mark.parametrize("damage", ["receipt", "version", "entrypoint"])
def test_verifier_rejects_missing_overlay_or_damaged_existing_entry(stage_setup, damage):
    store, lock, _calls, _selection = stage_setup
    _pin(store, lock, _bundle_files())
    entry = _publish(stage_setup, "linux-x64")
    dependency = entry / "lib/node_modules/npm/node_modules/brace-expansion"
    if damage == "receipt":
        (entry / ".npm-security-overlay.json").write_text('{}', encoding="utf-8")
    elif damage == "version":
        metadata = dependency / "package.json"
        metadata.write_text(json.dumps({"name": "brace-expansion", "version": "5.0.9"}), encoding="utf-8")
    else:
        (dependency / "dist/esm/index.js").unlink()
    assert get_package("npm").verify(entry, "linux-x64")
