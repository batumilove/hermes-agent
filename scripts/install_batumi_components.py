#!/usr/bin/env python3
"""Install commit-pinned Batumi extensions before Hermes startup."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_constants import get_hermes_home

DEFAULT_LOCK = ROOT / ".github" / "batumi-components.lock.yaml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VALID_KINDS = {"operations", "plugin-bundle", "skills"}


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


def _required_string(raw: dict[str, Any], key: str, prefix: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ComponentError(f"{prefix}.{key} must be a non-empty string")
    return value.strip()


def load_components(path: Path) -> list[Component]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
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
            )
        )
    return result


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
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
    _run(["git", "checkout", "--quiet", "--detach", component.commit], cwd=destination)
    actual = _run(["git", "rev-parse", "HEAD"], cwd=destination)
    if actual != component.commit:
        raise ComponentError(
            f"{component.component_id}: checked out {actual}, expected {component.commit}"
        )
    if _run(["git", "status", "--porcelain"], cwd=destination):
        raise ComponentError(f"{component.component_id}: checkout is not clean")


def install_component(
    component: Component,
    checkout: Path,
    *,
    hermes_home: Path,
    enable_plugins: bool,
) -> None:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
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
        for plugin in component.plugins:
            _run(
                [
                    sys.executable,
                    "-m",
                    "hermes_cli.main",
                    "plugins",
                    "enable",
                    plugin,
                ],
                cwd=ROOT,
                env=env,
            )


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
