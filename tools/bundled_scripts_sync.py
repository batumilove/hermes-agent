#!/usr/bin/env python3
"""Sync selected bundled helper scripts into stable runtime paths.

This is for repo-managed helper scripts that should survive `hermes update`
and fresh installs without forcing users to rewire cron or service config.
"""

from __future__ import annotations

from pathlib import Path

from hermes_constants import get_hermes_home

HERMES_HOME = get_hermes_home()
SCRIPTS_MANIFEST = HERMES_HOME / ".bundled_scripts_manifest"

# (repo-relative source path, destination path relative to ~/.hermes)
BUNDLED_SCRIPTS: list[tuple[str, str]] = [
    ("scripts/telegram-healthcheck-stateful.sh", "bin/telegram-healthcheck.sh"),
]

# Old managed destinations we should clean up when migrating to a new canonical path.
LEGACY_MANAGED_DESTINATIONS: list[str] = [
    str((Path.home() / ".local" / "bin" / "telegram-healthcheck-stateful").expanduser()),
]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_manifest() -> dict[str, str]:
    if not SCRIPTS_MANIFEST.exists():
        return {}
    result: dict[str, str] = {}
    try:
        for line in SCRIPTS_MANIFEST.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, target = line.partition(":")
            if name and target:
                result[name] = target
    except OSError:
        return {}
    return result


def _write_manifest(entries: dict[str, str]) -> None:
    SCRIPTS_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(f"{name}:{target}" for name, target in sorted(entries.items())) + "\n"
    tmp = SCRIPTS_MANIFEST.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(SCRIPTS_MANIFEST)


def _managed_destination(rel_dest: str) -> Path:
    return HERMES_HOME / rel_dest


def _unlink_if_managed(path_str: str, root: Path) -> bool:
    path = Path(path_str).expanduser()
    if not (path.exists() or path.is_symlink()):
        return False
    try:
        if path.is_symlink() and root in path.resolve().parents:
            path.unlink()
            return True
    except OSError:
        return False
    return False


def sync_bundled_scripts(quiet: bool = False) -> dict:
    root = _project_root()
    manifest = _read_manifest()

    linked: list[str] = []
    updated: list[str] = []
    missing: list[str] = []
    cleaned: list[str] = []

    expected_keys = {str(_managed_destination(rel_dest)) for _, rel_dest in BUNDLED_SCRIPTS}

    for rel_src, rel_dest in BUNDLED_SCRIPTS:
        src = root / rel_src
        dest = _managed_destination(rel_dest)
        manifest_key = str(dest)
        desired_target = str(src)

        if not src.exists():
            missing.append(str(dest))
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)

        changed = True
        if dest.is_symlink():
            try:
                changed = dest.resolve() != src.resolve()
            except OSError:
                changed = True
        elif dest.exists():
            changed = True

        if dest.exists() or dest.is_symlink():
            if changed:
                dest.unlink()
                dest.symlink_to(src)
                updated.append(str(dest))
            else:
                src.chmod(src.stat().st_mode | 0o111)
        else:
            dest.symlink_to(src)
            linked.append(str(dest))

        src.chmod(src.stat().st_mode | 0o111)
        manifest[manifest_key] = desired_target

    for old_dest in sorted(set(LEGACY_MANAGED_DESTINATIONS) | (set(manifest) - expected_keys)):
        if old_dest in expected_keys:
            continue
        if _unlink_if_managed(old_dest, root):
            cleaned.append(old_dest)
        manifest.pop(old_dest, None)

    _write_manifest(manifest)
    return {
        "linked": linked,
        "updated": updated,
        "missing": missing,
        "cleaned": cleaned,
    }


if __name__ == "__main__":
    result = sync_bundled_scripts(quiet=False)
    for name in result["linked"]:
        print(f"+ linked {name}")
    for name in result["updated"]:
        print(f"↻ updated {name}")
    for name in result["cleaned"]:
        print(f"− cleaned {name}")
    for name in result["missing"]:
        print(f"! missing source for {name}")
