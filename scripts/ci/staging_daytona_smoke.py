#!/usr/bin/env python3
"""Hermes staging sandbox smoke for local and Daytona CI lanes.

The script always creates a temporary HERMES_HOME and refuses production-looking
homes. With Daytona credentials it exercises the Daytona backend; without them
it performs an explicit local fallback and reports Daytona as skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DAYTONA_IMAGE = "python:3.11-slim"


def _run(cmd: list[str], *, env: dict[str, str], cwd: Path = REPO_ROOT, timeout: int = 120, check: bool = True) -> dict[str, object]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    result = {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "output": proc.stdout[-4000:],
    }
    if check and proc.returncode != 0:
        raise RuntimeError(json.dumps(result, indent=2))
    return result


def _safe_env(hermes_home: Path, mode: str) -> dict[str, str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    # Keep profile wrapper/config side effects inside the temp sandbox too.
    env["HOME"] = str(hermes_home / "home")
    env["XDG_CONFIG_HOME"] = str(hermes_home / "xdg-config")
    env["HERMES_STAGING_MODE"] = mode
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("HERMES_AUTO_APPROVE", "false")
    return env


def _ensure_isolated_home(hermes_home: Path, env: dict[str, str]) -> dict[str, object]:
    verify = REPO_ROOT / "scripts" / "ci" / "verify_profile_isolation.py"
    return _run(
        [sys.executable, str(verify), "--hermes-home", str(hermes_home), "--profile", "skill-lab"],
        env=env,
    )


def _run_local_smoke(hermes_home: Path, env: dict[str, str]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    hermes_cmd = [sys.executable, "-m", "hermes_cli.main"]
    results.append(_run(hermes_cmd + ["--version"], env=env))
    config_path = _run(hermes_cmd + ["config", "path"], env=env)
    results.append(config_path)
    config_output = str(config_path["output"]).strip()
    if str(hermes_home) not in config_output:
        raise RuntimeError(f"hermes config path escaped temp HERMES_HOME: {config_output!r} not under {hermes_home}")
    results.append(_run(hermes_cmd + ["profile", "create", "skill-lab"], env=env, check=False))
    results.append(_ensure_isolated_home(hermes_home, env))
    return results


def _run_daytona_smoke(env: dict[str, str]) -> dict[str, object]:
    if not (env.get("DAYTONA_API_KEY") and env.get("DAYTONA_API_URL")):
        return {
            "status": "skipped",
            "reason": "DAYTONA_API_KEY and DAYTONA_API_URL are not both set",
        }

    image = env.get("TERMINAL_DAYTONA_IMAGE") or DEFAULT_DAYTONA_IMAGE
    task_id = "ci-staging-smoke"
    try:
        from tools.environments.daytona import DaytonaEnvironment

        daytona_env = DaytonaEnvironment(
            image=image,
            timeout=90,
            persistent_filesystem=False,
            task_id=task_id,
        )
        try:
            result = daytona_env.execute("printf 'daytona-smoke-ok\\n' && pwd", timeout=90)
            output = str(result.get("output", ""))
            exit_code = int(result.get("returncode", 1))
            if exit_code != 0 or "daytona-smoke-ok" not in output:
                raise RuntimeError(f"Daytona exec failed exit={exit_code} output={output[-1000:]!r}")
            toolbox = getattr(getattr(daytona_env, "_sandbox", None), "toolbox_proxy_url", None)
            return {
                "status": "passed",
                "image": image,
                "task_id": task_id,
                "toolbox_proxy_url": toolbox,
                "output_tail": output[-1000:],
            }
        finally:
            daytona_env.cleanup()
    except Exception as exc:
        return {
            "status": "failed",
            "image": image,
            "task_id": task_id,
            "error": repr(exc),
        }


def _run_daytona_unit_tests_if_available(env: dict[str, str]) -> dict[str, object]:
    try:
        import daytona_sdk  # noqa: F401
    except Exception as exc:
        return {"status": "skipped", "reason": f"daytona_sdk import unavailable: {exc!r}"}

    result = _run(
        [sys.executable, "-m", "pytest", "tests/tools/test_daytona_environment.py", "-q", "-o", "addopts="],
        env=env,
        timeout=240,
        check=False,
    )
    return {
        "status": "passed" if result["exit_code"] == 0 else "failed",
        "pytest": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["auto", "local", "daytona"], default=os.environ.get("HERMES_STAGING_MODE", "auto"))
    parser.add_argument("--keep-home", action="store_true", help="Keep temp HERMES_HOME for debugging.")
    args = parser.parse_args()

    requested_mode = args.mode
    has_daytona = bool(os.environ.get("DAYTONA_API_KEY") and os.environ.get("DAYTONA_API_URL"))
    effective_mode = "daytona" if requested_mode == "auto" and has_daytona else "local" if requested_mode == "auto" else requested_mode

    hermes_home = Path(tempfile.mkdtemp(prefix="hermes-staging-smoke-"))
    env = _safe_env(hermes_home, effective_mode)

    report: dict[str, object] = {
        "repo_root": str(REPO_ROOT),
        "hermes_home": str(hermes_home),
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "daytona_credentials_present": has_daytona,
        "local": None,
        "daytona": None,
        "daytona_unit_tests": None,
    }

    exit_code = 0
    try:
        report["local"] = {"status": "passed", "steps": _run_local_smoke(hermes_home, env)}

        daytona_result = _run_daytona_smoke(env) if effective_mode == "daytona" else {
            "status": "skipped",
            "reason": "effective mode is local; Daytona credentials absent or local mode requested",
        }
        report["daytona"] = daytona_result
        if daytona_result.get("status") == "failed":
            exit_code = 1

        report["daytona_unit_tests"] = _run_daytona_unit_tests_if_available(env)
        if report["daytona_unit_tests"].get("status") == "failed":
            exit_code = 1

        if requested_mode == "daytona" and daytona_result.get("status") == "skipped":
            exit_code = 1
            report["error"] = "Daytona mode was explicitly requested but Daytona smoke was skipped."

        return exit_code
    except Exception as exc:
        report["error"] = repr(exc)
        return 1
    finally:
        if not args.keep_home:
            shutil.rmtree(hermes_home, ignore_errors=True)
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
