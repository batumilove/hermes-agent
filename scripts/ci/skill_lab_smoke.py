#!/usr/bin/env python3
"""Smoke-test an isolated Hermes `skill-lab` profile.

This script is intentionally safe by default:
- creates a temporary HERMES_HOME when --hermes-home is not provided;
- refuses to use production-looking Hermes homes;
- creates profiles/skill-lab only under the isolated home;
- performs a synthetic local skill write/read inside that profile only.

It is meant for staging/Daytona experiments where destructive skill work should
first prove it cannot touch the live ~/.hermes profile or shared skills tree.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_HOME = (Path.home() / ".hermes").resolve()
PROFILE_NAME = "skill-lab"
SYNTHETIC_SKILL_NAME = "skill-lab-probe"


def _resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except FileNotFoundError:
        return path.expanduser().absolute()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _refuse_unsafe_home(home: Path, *, allow_non_temp: bool) -> Path:
    resolved = _resolve(home)
    tmp_root = _resolve(Path(tempfile.gettempdir()))

    if resolved == PRODUCTION_HOME:
        raise SystemExit(f"Refusing production HERMES_HOME: {resolved}")
    if _is_relative_to(resolved, PRODUCTION_HOME):
        raise SystemExit(f"Refusing path inside production Hermes home: {resolved}")
    if not allow_non_temp and not _is_relative_to(resolved, tmp_root):
        raise SystemExit(
            f"Refusing non-temp HERMES_HOME without --allow-non-temp: {resolved} "
            f"(expected under {tmp_root})"
        )
    return resolved


def _write_minimal_profile_skill(profile_dir: Path) -> Path:
    skill_dir = profile_dir / "skills" / "staging" / SYNTHETIC_SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        textwrap.dedent(
            """
            ---
            name: skill-lab-probe
            description: Synthetic skill used only by the isolated skill-lab profile smoke.
            version: 0.0.1
            metadata:
              hermes:
                tags: [staging, smoke, isolation]
            ---

            # Skill Lab Probe

            If this text loads, the smoke read a skill from the isolated
            skill-lab profile, not from production `~/.hermes/skills`.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return skill_md


def _run_skill_probe(profile_dir: Path) -> dict:
    code = r'''
import json
import os
from pathlib import Path

from tools import skills_tool

profile_dir = Path(os.environ["HERMES_HOME"]).resolve()
result = {
    "module_hermes_home": str(skills_tool.HERMES_HOME.resolve()),
    "module_skills_dir": str(skills_tool.SKILLS_DIR.resolve()),
}
listing = json.loads(skills_tool.skills_list())
view = json.loads(skills_tool.skill_view("skill-lab-probe", preprocess=False))
result["list_success"] = listing.get("success") is True
result["listed_names"] = [s.get("name") for s in listing.get("skills", [])]
result["view_success"] = view.get("success") is True
raw_view_path = view.get("path", "")
if raw_view_path:
    view_path = Path(raw_view_path)
    if not view_path.is_absolute():
        view_path = Path(result["module_skills_dir"]) / view_path
    result["view_path"] = str(view_path.resolve())
else:
    result["view_path"] = ""
result["content_has_probe"] = "Skill Lab Probe" in view.get("content", "")
result["all_paths_under_profile"] = (
    Path(result["module_hermes_home"]).resolve() == profile_dir
    and Path(result["module_skills_dir"]).resolve() == profile_dir / "skills"
    and (not result["view_path"] or Path(result["view_path"]).resolve().is_relative_to(profile_dir))
)
print(json.dumps(result, sort_keys=True))
'''
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_dir)
    env.pop("HERMES_PROFILE", None)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "skill probe failed with exit code "
            f"{completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _create_profile(isolated_home: Path) -> Path:
    code = r'''
import json
import os
from pathlib import Path
from hermes_cli.profiles import create_profile, get_profile_dir, profile_exists

name = "skill-lab"
if not profile_exists(name):
    create_profile(name, no_alias=True, no_skills=True, description="Temporary isolated staging skill lab profile")
profile_dir = get_profile_dir(name)
print(json.dumps({"profile_dir": str(profile_dir.resolve())}))
'''
    env = os.environ.copy()
    env["HERMES_HOME"] = str(isolated_home)
    env.pop("HERMES_PROFILE", None)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "profile creation failed with exit code "
            f"{completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    data = json.loads(completed.stdout.strip().splitlines()[-1])
    return Path(data["profile_dir"]).resolve()


def run(hermes_home: Path | None, *, keep: bool, allow_non_temp: bool) -> dict:
    temp_ctx = None
    if hermes_home is None:
        temp_ctx = tempfile.TemporaryDirectory(prefix="hermes-skill-lab-")
        hermes_home = Path(temp_ctx.name) / ".hermes"

    isolated_home = _refuse_unsafe_home(hermes_home, allow_non_temp=allow_non_temp)
    isolated_home.mkdir(parents=True, exist_ok=True)

    before_prod_sentinel = PRODUCTION_HOME / "profiles" / PROFILE_NAME / ".skill-lab-smoke-sentinel"
    if before_prod_sentinel.exists():
        raise SystemExit(f"Unexpected production sentinel already exists: {before_prod_sentinel}")

    profile_dir = _create_profile(isolated_home)
    expected_profile_dir = isolated_home / "profiles" / PROFILE_NAME
    if profile_dir != expected_profile_dir.resolve():
        raise AssertionError(f"profile_dir={profile_dir} expected={expected_profile_dir.resolve()}")
    if not _is_relative_to(profile_dir, isolated_home):
        raise AssertionError(f"profile escaped isolated home: {profile_dir}")
    if _is_relative_to(profile_dir, PRODUCTION_HOME):
        raise AssertionError(f"profile unexpectedly under production home: {profile_dir}")

    skill_md = _write_minimal_profile_skill(profile_dir)
    probe = _run_skill_probe(profile_dir)

    expected_checks = {
        "list_success": probe.get("list_success") is True,
        "skill_listed": SYNTHETIC_SKILL_NAME in probe.get("listed_names", []),
        "view_success": probe.get("view_success") is True,
        "content_has_probe": probe.get("content_has_probe") is True,
        "all_paths_under_profile": probe.get("all_paths_under_profile") is True,
        "production_sentinel_absent": not before_prod_sentinel.exists(),
        "production_profile_not_created_by_smoke": not (PRODUCTION_HOME / "profiles" / PROFILE_NAME).exists(),
        "production_skills_probe_absent": not (PRODUCTION_HOME / "skills" / "staging" / SYNTHETIC_SKILL_NAME).exists(),
    }
    failed = [name for name, ok in expected_checks.items() if not ok]
    if failed:
        raise AssertionError(f"skill-lab smoke failed checks: {failed}; probe={probe}")

    result = {
        "success": True,
        "hermes_home": str(isolated_home),
        "profile": PROFILE_NAME,
        "profile_dir": str(profile_dir),
        "synthetic_skill": str(skill_md),
        "checks": expected_checks,
        "probe": probe,
        "shared_skills_staging_convention": "Use ~/hermes-skills-staging for staging shared-skill experiments; do not point staging at ~/hermes-skills or production ~/.hermes/skills.",
    }

    if temp_ctx is not None and keep:
        # Prevent TemporaryDirectory cleanup so operators can inspect artifacts.
        temp_ctx.cleanup = lambda: None  # type: ignore[method-assign]
        result["kept_temp_home"] = str(isolated_home)
    elif temp_ctx is not None:
        # Keep the JSON honest: the directory existed during the smoke and will
        # be cleaned as the process exits.
        result["temp_home_cleanup"] = "on_exit"

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, help="Isolated Hermes home to use; defaults to a temporary directory.")
    parser.add_argument("--allow-non-temp", action="store_true", help="Allow a non-temp HERMES_HOME for staging VMs. Production ~/.hermes is still refused.")
    parser.add_argument("--keep", action="store_true", help="Keep the generated temporary HERMES_HOME for inspection.")
    args = parser.parse_args(argv)

    result = run(args.hermes_home, keep=args.keep, allow_non_temp=args.allow_non_temp)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
