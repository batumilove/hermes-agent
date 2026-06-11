#!/usr/bin/env python3
"""Verify CI/staging profile isolation for Hermes.

This script is intentionally conservative: in CI mode it refuses to run with a
production-looking HERMES_HOME and only writes inside an ephemeral home.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

PRODUCTION_HOMES = {
    Path("/home/ubuntu/.hermes").resolve(),
    Path.home().joinpath(".hermes").resolve(),
}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _looks_temp(path: Path) -> bool:
    tmp = Path(tempfile.gettempdir()).resolve()
    return _is_relative_to(path, tmp)


def validate_hermes_home(path: str | os.PathLike[str], *, ci: bool = True) -> Path:
    home = Path(path).expanduser().resolve()
    if home in PRODUCTION_HOMES:
        raise SystemExit(f"Refusing production HERMES_HOME: {home}")
    if any(_is_relative_to(home, prod) for prod in PRODUCTION_HOMES):
        raise SystemExit(f"Refusing production-profile-adjacent HERMES_HOME: {home}")
    if ci and not _looks_temp(home):
        raise SystemExit(f"CI isolation requires temp HERMES_HOME, got: {home}")
    return home


def verify_profile_isolation(hermes_home: str | os.PathLike[str], *, profile: str = "skill-lab", ci: bool = True) -> dict[str, object]:
    home = validate_hermes_home(hermes_home, ci=ci)
    home.mkdir(parents=True, exist_ok=True)

    profile_root = home / "profiles" / profile
    skill_root = profile_root / "skills" / "ci-isolation-smoke"
    skill_root.mkdir(parents=True, exist_ok=True)
    sentinel = skill_root / "SENTINEL.txt"
    sentinel.write_text("ci isolation smoke\n", encoding="utf-8")

    production_sentinel_paths = [
        prod / "profiles" / profile / "skills" / "ci-isolation-smoke" / "SENTINEL.txt"
        for prod in PRODUCTION_HOMES
    ]
    touched_production = [str(p) for p in production_sentinel_paths if p.exists()]
    if touched_production:
        raise SystemExit(
            "Production profile sentinel unexpectedly exists: " + ", ".join(touched_production)
        )

    return {
        "hermes_home": str(home),
        "profile": profile,
        "profile_root": str(profile_root),
        "sentinel": str(sentinel),
        "production_homes_checked": [str(p) for p in sorted(PRODUCTION_HOMES)],
        "production_sentinel_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME"))
    parser.add_argument("--profile", default="skill-lab")
    parser.add_argument("--no-ci", action="store_true", help="Do not require the home to live under tempfile.gettempdir().")
    parser.add_argument("--keep", action="store_true", help="Keep an auto-created temp home after verification.")
    args = parser.parse_args()

    created_temp: Path | None = None
    hermes_home = args.hermes_home
    if not hermes_home:
        created_temp = Path(tempfile.mkdtemp(prefix="hermes-ci-isolation-"))
        hermes_home = str(created_temp)

    try:
        result = verify_profile_isolation(hermes_home, profile=args.profile, ci=not args.no_ci)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        if created_temp is not None and not args.keep:
            shutil.rmtree(created_temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
