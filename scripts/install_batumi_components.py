#!/usr/bin/env python3
"""Install commit-pinned Batumi extensions before Hermes startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_constants import get_hermes_home

DEFAULT_LOCK = ROOT / ".github" / "batumi-components.lock.yaml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VALID_KINDS = {"operations", "plugin-bundle", "skills", "catalog-plugin"}
DEFAULT_OVERLAYS = ROOT / "patches" / "external-plugins" / "overlays.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class ComponentError(RuntimeError):
    """Raised when a component cannot be verified or installed."""


@dataclass(frozen=True)
class Component:
    component_id: str
    repository: str
    url: str
    commit: str
    kind: str
    plugins: tuple[str, ...]
    subdir: str | None = None
    overlay: str | None = None


def _required_string(raw: dict[str, Any], key: str, prefix: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentError(f"{prefix}.{key} must be a non-empty string")
    return value.strip()


def _repo_relative_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ComponentError(f"{label} must be a repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ComponentError(f"{label} must be a repository-relative path without traversal")
    return Path(*path.parts)


def _validate_catalog_component(component: Component) -> None:
    if component.kind != "catalog-plugin":
        return
    _repo_relative_path(component.subdir, f"{component.component_id}.subdir")
    if not isinstance(component.overlay, str) or not component.overlay.strip():
        raise ComponentError(f"{component.component_id}.overlay must be a non-empty string")
    if len(component.plugins) != 1 or not PLUGIN_NAME_RE.fullmatch(component.plugins[0]):
        raise ComponentError(f"{component.component_id}: catalog-plugin requires one named plugin")


def load_components(path: Path) -> list[Component]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ComponentError(f"could not read component lock: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ComponentError("component lock version must be 1")
    entries = raw.get("components")
    if not isinstance(entries, list) or not entries:
        raise ComponentError("components must be a non-empty list")

    result: list[Component] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"components[{index}]"
        if not isinstance(entry, dict):
            raise ComponentError(f"{prefix} must be a mapping")
        component_id = _required_string(entry, "id", prefix)
        if component_id in seen:
            raise ComponentError(f"duplicate component id: {component_id}")
        seen.add(component_id)
        repository = _required_string(entry, "repository", prefix)
        url = _required_string(entry, "url", prefix)
        commit = _required_string(entry, "commit", prefix)
        kind = _required_string(entry, "kind", prefix)
        if not SHA_RE.fullmatch(commit):
            raise ComponentError(f"{prefix}.commit must be a full lowercase SHA")
        if kind not in VALID_KINDS:
            raise ComponentError(f"{prefix}.kind must be one of {sorted(VALID_KINDS)}")
        plugins = entry.get("plugins")
        if not isinstance(plugins, list) or not all(
            isinstance(plugin, str) and plugin.strip() for plugin in plugins
        ):
            raise ComponentError(f"{prefix}.plugins must be a string list")
        result.append(
            Component(
                component_id,
                repository,
                url,
                commit,
                kind,
                tuple(plugin.strip() for plugin in plugins),
                entry.get("subdir"),
                entry.get("overlay"),
            )
        )
    for component in result:
        _validate_catalog_component(component)
    return result


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ComponentError(detail[0] if detail else f"{args[0]} failed")
    return result.stdout.strip()


def checkout_component(component: Component, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _run(["git", "init", "--quiet"], cwd=destination)
    _run(["git", "remote", "add", "origin", component.url], cwd=destination)
    _run(
        ["git", "fetch", "--quiet", "--depth", "1", "origin", component.commit],
        cwd=destination,
    )
    fetched = _run(["git", "rev-parse", "FETCH_HEAD"], cwd=destination)
    if fetched != component.commit:
        raise ComponentError(
            f"{component.component_id}: fetched {fetched}, expected {component.commit}"
        )
    checkout_args = ["git"]
    if component.kind == "catalog-plugin":
        # Reviewed source hashes describe Git blob bytes, not host CRLF conversion.
        checkout_args += ["-c", "core.autocrlf=false"]
    _run(checkout_args + ["checkout", "--quiet", "--detach", component.commit], cwd=destination)
    actual = _run(["git", "rev-parse", "HEAD"], cwd=destination)
    if actual != component.commit:
        raise ComponentError(
            f"{component.component_id}: checked out {actual}, expected {component.commit}"
        )
    if _run(["git", "status", "--porcelain"], cwd=destination):
        raise ComponentError(f"{component.component_id}: checkout is not clean")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_url(value: str) -> str:
    return value.rstrip("/").removesuffix(".git")


def _catalog_overlay(component: Component, checkout: Path, manifest: Path) -> tuple[dict[str, Any], Path, Path]:
    _validate_catalog_component(component)
    try:
        document = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ComponentError(f"could not read overlay registry: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("overlays"), list):
        raise ComponentError("overlay registry must have version 1 and an overlays list")
    matches = [row for row in document["overlays"] if isinstance(row, dict) and row.get("id") == component.overlay]
    if len(matches) != 1:
        raise ComponentError(f"{component.component_id}: overlay must identify one reviewed entry")
    overlay = matches[0]
    repository_url = _repository_url(component.url)
    if (not isinstance(overlay.get("repository"), str)
            or _repository_url(overlay["repository"]) != repository_url
            or repository_url != f"https://github.com/{component.repository}"
            or overlay.get("commit") != component.commit
            or overlay.get("subdir") != component.subdir):
        raise ComponentError(f"{component.component_id}: overlay repository/commit/subdir identity mismatch")
    if _run(["git", "rev-parse", "HEAD"], cwd=checkout) != component.commit:
        raise ComponentError(f"{component.component_id}: catalog checkout commit mismatch")
    if _run(["git", "status", "--porcelain"], cwd=checkout):
        raise ComponentError(f"{component.component_id}: catalog checkout is not clean")
    source = checkout / _repo_relative_path(component.subdir, "overlay subdir")
    if not source.is_dir() or not source.resolve().is_relative_to(checkout.resolve()):
        raise ComponentError(f"{component.component_id}: catalog plugin subdir is missing or escapes checkout")
    patch = ROOT / _repo_relative_path(overlay.get("patch"), "overlay patch")
    expected_patch_hash = overlay.get("sha256")
    if (not isinstance(expected_patch_hash, str) or not SHA256_RE.fullmatch(expected_patch_hash)
            or not patch.is_file() or patch.is_symlink() or not patch.resolve().is_relative_to(ROOT.resolve())
            or _sha256(patch) != expected_patch_hash):
        raise ComponentError(f"{component.component_id}: overlay patch SHA256 mismatch")
    source_hashes = overlay.get("source_sha256")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise ComponentError(f"{component.component_id}: overlay source SHA256 map is required")
    for name, expected in source_hashes.items():
        path = source / _repo_relative_path(name, "overlay source")
        if (not isinstance(expected, str) or not SHA256_RE.fullmatch(expected)
                or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source.resolve())
                or _sha256(path) != expected):
            raise ComponentError(f"{component.component_id}: overlay source SHA256 mismatch for {name}")
    return overlay, source, patch


def _enable_component_plugins(component: Component, hermes_home: Path, env: dict[str, str]) -> None:
    for plugin in component.plugins:
        _run([sys.executable, "-m", "hermes_cli.main", "plugins", "enable", plugin], cwd=ROOT, env=env)


def _install_catalog_plugin(component: Component, checkout: Path, *, hermes_home: Path,
                            enable_plugins: bool, env: dict[str, str], overlay_manifest: Path) -> None:
    overlay, source, patch = _catalog_overlay(component, checkout, overlay_manifest)
    plugins_dir = hermes_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / component.plugins[0]
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ComponentError(f"{component.component_id}: existing plugin target is not a directory")
    try:
        with tempfile.TemporaryDirectory(prefix=f".{target.name}.batumi-stage-", dir=plugins_dir) as temporary:
            staging = Path(temporary)
            candidate = staging / "candidate"
            # Keep the previous tree outside automatic staging cleanup so even a
            # filesystem failure during rollback cannot delete the recovery copy.
            previous = plugins_dir / f"{staging.name}.previous"
            shutil.copytree(source, candidate, symlinks=True)
            # Give git an isolated root even when HERMES_HOME is inside another checkout.
            _run(["git", "init", "--quiet"], cwd=candidate)
            # Check and apply ONLY the reviewed, hash-verified patch to the copied pinned subdir.
            _run(["git", "apply", "--check", str(patch)], cwd=candidate)
            _run(["git", "apply", str(patch)], cwd=candidate)
            shutil.rmtree(candidate / ".git")
            provenance = {
                "component": component.component_id, "repository": component.repository,
                "url": component.url, "commit": component.commit, "subdir": component.subdir,
                "overlay": component.overlay, "patch_sha256": overlay["sha256"],
                "source_sha256": overlay["source_sha256"],
                "installed_sha256": {name: _sha256(candidate / name) for name in overlay["source_sha256"]},
            }
            (candidate / ".batumi-component.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            published = False
            if target.exists():
                os.replace(target, previous)
            try:
                os.replace(candidate, target)
                published = True
                if enable_plugins:
                    _enable_component_plugins(component, hermes_home, env)
            except BaseException:
                if published:
                    shutil.rmtree(target)
                if previous.exists():
                    try:
                        os.replace(previous, target)
                    except OSError as rollback_error:
                        raise ComponentError(
                            f"{component.component_id}: rollback failed; previous plugin retained at {previous}: {rollback_error}"
                        ) from rollback_error
                raise
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
    except OSError as exc:
        raise ComponentError(f"{component.component_id}: catalog plugin publication failed: {exc}") from exc


def install_component(
    component: Component,
    checkout: Path,
    *,
    hermes_home: Path,
    enable_plugins: bool,
    overlay_manifest: Path | None = None,
) -> None:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    if component.kind == "catalog-plugin":
        _install_catalog_plugin(component, checkout, hermes_home=hermes_home,
                                enable_plugins=enable_plugins, env=env,
                                overlay_manifest=overlay_manifest or DEFAULT_OVERLAYS)
        return
    if component.kind == "plugin-bundle":
        installer = checkout / "install.sh"
        if not installer.is_file():
            raise ComponentError(f"{component.component_id}: install.sh is missing")
        _run(
            [str(installer), "--hermes-home", str(hermes_home)],
            cwd=checkout,
            env=env,
        )
    elif component.kind == "skills":
        installer = checkout / "scripts" / "install-local.sh"
        if not installer.is_file():
            raise ComponentError(
                f"{component.component_id}: scripts/install-local.sh is missing"
            )
        env["HERMES_SKILLS_SKIP_PULL"] = "1"
        _run([str(installer)], cwd=checkout, env=env)

    if enable_plugins:
        _enable_component_plugins(component, hermes_home, env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--hermes-home", type=Path)
    parser.add_argument("--component", action="append", default=[])
    parser.add_argument("--no-enable", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        components = load_components(args.lock.resolve())
        selected = set(args.component)
        if selected:
            known = {component.component_id for component in components}
            unknown = sorted(selected - known)
            if unknown:
                raise ComponentError(f"unknown components: {', '.join(unknown)}")
            components = [
                component
                for component in components
                if component.component_id in selected
            ]

        hermes_home = (args.hermes_home or get_hermes_home()).expanduser().resolve()
        cache_dir = (
            args.cache_dir or hermes_home / "components"
        ).expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        for component in components:
            destination = cache_dir / component.component_id
            checkout_component(component, destination)
            if args.verify_only and component.kind == "catalog-plugin":
                _catalog_overlay(component, destination, DEFAULT_OVERLAYS)
            if not args.verify_only:
                install_component(
                    component,
                    destination,
                    hermes_home=hermes_home,
                    enable_plugins=not args.no_enable,
                )
            print(
                f"{component.component_id}: verified {component.commit}"
                + (" (installed)" if not args.verify_only else "")
            )
    except ComponentError as exc:
        print(f"component installation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
