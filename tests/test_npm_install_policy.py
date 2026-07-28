"""Repository-wide npm install cooldown policy checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_NPM = "11.18.0"
REQUIRED_COOLDOWN_DAYS = 7
NPM_INSTALL_RE = re.compile(r"\bnpm\s+(?:ci|install|i|clean-install|ic)(?:\s|$)")
PINNED_NPM_RE = re.compile(
    rf"\bnpm\s+exec\s+--package=npm@{re.escape(REQUIRED_NPM)}\b"
)
COOLDOWN_RE = re.compile(r"--min-release-age(?:=|\s+)(\d+)\b")


def _command_strings(value: Any) -> list[str]:
    """Collect shell strings from structured workflow run/command fields."""
    if isinstance(value, dict):
        commands: list[str] = []
        for key, child in value.items():
            if key in {"run", "command"} and isinstance(child, str):
                commands.append(child)
            else:
                commands.extend(_command_strings(child))
        return commands
    if isinstance(value, list):
        return [command for child in value for command in _command_strings(child)]
    return []


def _install_segments(command: str) -> list[str]:
    logical_commands = re.sub(r"[ \t]*\\\r?\n[ \t]*", " ", command)
    return [
        segment.strip().rstrip(" \\")
        for segment in re.split(r"\s*(?:&&|\|\||;|\r?\n)\s*", logical_commands)
        if NPM_INSTALL_RE.search(segment)
    ]


def _install_surfaces() -> list[tuple[str, str]]:
    surfaces: list[tuple[str, str]] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for command in _command_strings(parsed):
            surfaces.extend(
                (str(workflow.relative_to(ROOT)), segment)
                for segment in _install_segments(command)
            )

    for dockerfile in sorted(ROOT.glob("Dockerfile*")):
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("#"):
                surfaces.extend(
                    (str(dockerfile.relative_to(ROOT)), segment)
                    for segment in _install_segments(line)
                )
    return surfaces


def _uses_supported_cooldown(command: str) -> bool:
    npm_exec, separator, inner_command = command.partition(" -- ")
    if not separator or not PINNED_NPM_RE.search(npm_exec):
        return False
    if not NPM_INSTALL_RE.search(inner_command):
        return False
    cooldowns = [int(value) for value in COOLDOWN_RE.findall(inner_command)]
    return bool(cooldowns) and cooldowns[-1] >= REQUIRED_COOLDOWN_DAYS


def test_multiline_commands_are_checked_independently() -> None:
    command = (
        "npm exec --package=npm@11.18.0 -- npm ci --min-release-age=7\n"
        "npm ci"
    )

    segments = _install_segments(command)

    assert len(segments) == 2
    assert _uses_supported_cooldown(segments[0])
    assert not _uses_supported_cooldown(segments[1])


def test_shell_line_continuations_remain_one_command() -> None:
    command = (
        "npm exec --package=npm@11.18.0 -- \\\n"
        "  npm ci --min-release-age=7"
    )

    assert _install_segments(command) == [
        "npm exec --package=npm@11.18.0 -- npm ci --min-release-age=7"
    ]


def test_cooldown_must_be_on_inner_npm_11_command() -> None:
    command = (
        "npm exec --package=npm@11.18.0 --min-release-age=7 -- "
        "npm ci --ignore-scripts"
    )

    assert not _uses_supported_cooldown(command)


def test_every_ci_npm_install_uses_supported_cooldown() -> None:
    failures: list[str] = []
    surfaces = _install_surfaces()

    assert surfaces, "no npm install surfaces discovered"
    for path, command in surfaces:
        if not _uses_supported_cooldown(command):
            failures.append(f"{path}: {command}")

    assert not failures, "npm installs bypass pinned cooldown policy:\n" + "\n".join(failures)
