#!/usr/bin/env python3
"""Reproducible Daytona real-repo smoke runner.

Creates a Daytona sandbox, uploads a clean repository snapshot, installs the
repo into an in-sandbox virtualenv, runs meaningful tests, and writes the same
artifact shape used by the first manual Daytona evaluation:

- manifest.json
- commands.ndjson
- transcript.log
- cleanup.json
- stale-check.json
- hermes-agent-head.tar

The runner intentionally avoids xtrace, env dumps, and secret values in
artifacts. Use ``--mode dry-run`` for artifact/layout validation without
Daytona credentials.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = Path.home() / ".hermes" / "artifacts" / "daytona-eval"
DEFAULT_DAYTONA_IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20"
DEFAULT_TESTS = [".venv/bin/python -m pytest tests/tools/test_daytona_environment.py -q -o addopts="]
_SECRET_PATTERNS = [
    re.compile(r"(?i)(DAYTONA_API_KEY\s*=\s*)([^\s'\"]+)"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)([^\s'\"]+)"),
    re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._~+\-/=]{8,})"),
]


@dataclasses.dataclass(frozen=True)
class RemoteStep:
    phase: str
    cmd: str
    cwd: str
    timeout: int | None = 900


@dataclasses.dataclass(frozen=True)
class CommandResult:
    phase: str
    cmd: str
    cwd: str
    timeout: int | None
    duration_s: float
    returncode: int
    output: str

    def to_json(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def utc_run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def redact(text: object) -> str:
    redacted = str(text)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: match.group(1) + "<redacted>", redacted)
    api_key = os.environ.get("DAYTONA_API_KEY")
    if api_key:
        redacted = redacted.replace(api_key, "<redacted>")
    return redacted


class ArtifactWriter:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.commands_file = self.run_dir / "commands.ndjson"
        self.transcript_file = self.run_dir / "transcript.log"
        self.commands_file.write_text("", encoding="utf-8")
        self.transcript_file.write_text("", encoding="utf-8")
        self.commands: list[dict[str, object]] = []

    def log(self, message: str) -> None:
        with self.transcript_file.open("a", encoding="utf-8") as fh:
            fh.write(redact(message).rstrip() + "\n")

    def record_command(self, result: CommandResult) -> None:
        payload = result.to_json()
        payload["cmd"] = redact(payload["cmd"])
        payload["output"] = redact(payload["output"])
        self.commands.append(payload)
        with self.commands_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
        self.log(f"$ ({result.cwd}) {result.cmd}\nrc={result.returncode} elapsed={result.duration_s:.3f}s\n{result.output[-4000:]}")

    def write_json(self, name: str, payload: dict[str, object]) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(_redact_json(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _redact_json(value):
    if isinstance(value, dict):
        return {str(k): _redact_json(v) for k, v in value.items() if str(k).lower() not in {"daytona_api_key", "api_key"}}
    if isinstance(value, list):
        return [_redact_json(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


def _subprocess_result(cmd: Sequence[str], *, cwd: Path, timeout: int = 120) -> CommandResult:
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
    return CommandResult(
        phase="host",
        cmd=" ".join(shlex.quote(part) for part in cmd),
        cwd=str(cwd),
        timeout=timeout,
        duration_s=round(time.monotonic() - start, 3),
        returncode=proc.returncode,
        output=proc.stdout[-8000:],
    )


def create_repo_archive(repo_root: Path, archive_path: Path, writer: ArtifactWriter | None = None) -> None:
    """Create a clean source archive.

    Prefer ``git archive HEAD`` when possible, which avoids untracked local
    files and .git metadata. Fall back to a deterministic tar of the directory
    for dry-run tests or non-git checkouts.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    git_dir = repo_root / ".git"
    if git_dir.exists():
        result = _subprocess_result(["git", "archive", "--format=tar", "--output", str(archive_path), "HEAD"], cwd=repo_root, timeout=120)
        if writer:
            writer.record_command(result)
        if result.returncode == 0:
            return
        if writer:
            writer.log("git archive failed; falling back to filesystem tar")

    with tarfile.open(archive_path, "w") as tf:
        for path in sorted(repo_root.rglob("*")):
            rel = path.relative_to(repo_root)
            if any(part in {".git", "__pycache__", ".pytest_cache", ".mypy_cache"} for part in rel.parts):
                continue
            if path == archive_path or archive_path in path.parents:
                continue
            tf.add(path, arcname=str(rel), recursive=False)


def _venv_test_command(test_cmd: str) -> str:
    if test_cmd.startswith("python3 -m pytest "):
        return ".venv/bin/python -m pytest " + test_cmd.removeprefix("python3 -m pytest ")
    if test_cmd.startswith("python -m pytest "):
        return ".venv/bin/python -m pytest " + test_cmd.removeprefix("python -m pytest ")
    return test_cmd


def build_remote_plan(*, sandbox_home: str, archive_remote_path: str, tests: Sequence[str]) -> list[RemoteStep]:
    remote_repo = f"{sandbox_home.rstrip('/')}/hermes-agent"
    normalized_tests = [_venv_test_command(test_cmd) for test_cmd in tests]
    return [
        RemoteStep("remote", "printf '%s\\n' \"$HOME\"", sandbox_home, 300),
        RemoteStep("remote", f"mkdir -p {shlex.quote(remote_repo)} && tar -xf {shlex.quote(archive_remote_path)} -C {shlex.quote(remote_repo)}", sandbox_home, 900),
        RemoteStep("remote", "python3 --version && pwd", remote_repo, 300),
        RemoteStep("remote", "python3 -m venv .venv", remote_repo, 900),
        RemoteStep("remote", ".venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip setuptools wheel", remote_repo, 1200),
        RemoteStep("remote", ".venv/bin/python -m pip install --disable-pip-version-check -q -e '.[dev,daytona]'", remote_repo, 1800),
        *[RemoteStep("remote", test_cmd, remote_repo, 1800) for test_cmd in normalized_tests],
    ]


def _host_git(cmd: Sequence[str], repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(["git", *cmd], cwd=str(repo_root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30, check=False)
    except Exception:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _dry_run(writer: ArtifactWriter, archive_path: Path, repo_root: Path, tests: Sequence[str]) -> dict[str, object]:
    sandbox_home = "/root"
    remote_repo = f"{sandbox_home}/hermes-agent"
    writer.record_command(CommandResult("upload", f"upload_file {archive_path} -> /tmp/hermes-agent-head.tar", "host", None, 0.0, 0, "dry-run: would upload repo archive"))
    for step in build_remote_plan(sandbox_home=sandbox_home, archive_remote_path="/tmp/hermes-agent-head.tar", tests=tests):
        writer.record_command(CommandResult(step.phase, step.cmd, step.cwd, step.timeout, 0.0, 0, "dry-run: not executed"))
    return {
        "status": "passed",
        "mode": "dry-run",
        "sandbox_id": "dry-run",
        "sandbox_name": "hermes-realrepo-smoke-dry-run",
        "sandbox_home": sandbox_home,
        "remote_repo": remote_repo,
        "cleanup": {"cleanup": "dry-run", "sandbox_id": "dry-run", "sandbox_name": "hermes-realrepo-smoke-dry-run"},
        "stale_check": {"active": [], "all": [], "sandbox_id": "dry-run", "sandbox_name": "hermes-realrepo-smoke-dry-run"},
    }


def _list_stale(client, query_cls, labels: dict[str, str], sandbox_name: str) -> dict[str, object]:
    active: list[object] = []
    all_matches: list[object] = []
    try:
        results = client.list(query_cls(labels=labels, limit=100))
        for item in results:
            payload = {
                "id": getattr(item, "id", None),
                "name": getattr(item, "name", None),
                "state": str(getattr(item, "state", "")),
            }
            all_matches.append(payload)
            state = payload["state"].lower()
            if payload["name"] == sandbox_name and not any(marker in state for marker in ["deleted", "archived", "stopped"]):
                active.append(payload)
    except Exception as exc:
        return {"active": [f"stale-check-failed: {redact(repr(exc))}"], "all": all_matches, "sandbox_name": sandbox_name}
    return {"active": active, "all": all_matches, "sandbox_name": sandbox_name}


def _run_daytona(writer: ArtifactWriter, archive_path: Path, *, image: str, tests: Sequence[str], run_id: str) -> dict[str, object]:
    from tools.environments.daytona import DaytonaEnvironment

    sandbox_task_id = f"realrepo-smoke-{run_id.lower()}"
    labels = {"hermes_task_id": sandbox_task_id}
    env = DaytonaEnvironment(
        image=image,
        cwd="~",
        timeout=900,
        persistent_filesystem=False,
        task_id=sandbox_task_id,
        cpu=2,
        memory=4096,
        disk=10240,
    )
    sandbox = env._sandbox
    client = env._daytona
    query_cls = env._ListSandboxesQuery
    sandbox_id = getattr(sandbox, "id", None)
    sandbox_name = getattr(sandbox, "name", f"hermes-{sandbox_task_id}")
    cleanup_payload: dict[str, object]
    stale_payload: dict[str, object]
    remote_archive = "/tmp/hermes-agent-head.tar"
    failed = False
    try:
        upload_start = time.monotonic()
        sandbox.fs.upload_file(str(archive_path), remote_archive)
        writer.record_command(CommandResult("upload", f"upload_file {archive_path} -> {remote_archive}", "host", None, round(time.monotonic() - upload_start, 3), 0, "uploaded repo archive"))

        home_result = env.execute("printf '%s\\n' \"$HOME\"", cwd=env.cwd, timeout=300)
        home = str(home_result.get("output", "")).strip().splitlines()[-1] if str(home_result.get("output", "")).strip() else getattr(env, "_remote_home", env.cwd)
        if not home.startswith("/"):
            home = getattr(env, "_remote_home", "/root")
        env.cwd = home
        writer.record_command(CommandResult("remote", "printf '%s\\n' \"$HOME\"", home, 300, 0.0, int(home_result.get("returncode", 1)), str(home_result.get("output", ""))))

        for step in build_remote_plan(sandbox_home=home, archive_remote_path=remote_archive, tests=tests)[1:]:
            start = time.monotonic()
            result = env.execute(step.cmd, cwd=step.cwd, timeout=step.timeout)
            rc = int(result.get("returncode", 1))
            writer.record_command(CommandResult(step.phase, step.cmd, step.cwd, step.timeout, round(time.monotonic() - start, 3), rc, str(result.get("output", ""))))
            if rc != 0:
                failed = True
                break
    finally:
        try:
            env.cleanup()
            cleanup_payload = {"cleanup": "ok", "sandbox_id": sandbox_id, "sandbox_name": sandbox_name}
        except Exception as exc:
            cleanup_payload = {"cleanup": "failed", "sandbox_id": sandbox_id, "sandbox_name": sandbox_name, "error": repr(exc)}
            failed = True
        stale_payload = _list_stale(client, query_cls, labels, sandbox_name)
        if stale_payload.get("active"):
            failed = True

    return {
        "status": "failed" if failed else "passed",
        "mode": "daytona",
        "sandbox_id": sandbox_id,
        "sandbox_name": sandbox_name,
        "sandbox_home": getattr(env, "_remote_home", None),
        "remote_repo": f"{getattr(env, '_remote_home', '/root')}/hermes-agent",
        "cleanup": cleanup_payload,
        "stale_check": stale_payload,
    }


def run_smoke(*, repo_root: Path = REPO_ROOT, run_dir: Path | None = None, mode: str = "daytona", run_id: str | None = None, tests: Sequence[str] | None = None, image: str | None = None) -> int:
    run_id = run_id or utc_run_id()
    tests = list(tests or DEFAULT_TESTS)
    run_dir = run_dir or DEFAULT_ARTIFACT_ROOT / f"{run_id}-daytona-realrepo"
    writer = ArtifactWriter(run_dir)
    archive_path = run_dir / "hermes-agent-head.tar"
    create_repo_archive(repo_root, archive_path, writer)

    writer.log(f"Daytona real-repo smoke start run_id={run_id} mode={mode} repo={repo_root}")
    if mode == "dry-run":
        result = _dry_run(writer, archive_path, repo_root, tests)
    else:
        missing_credentials = [name for name in ("DAYTONA_API_KEY", "DAYTONA_API_URL") if not os.environ.get(name)]
        if missing_credentials:
            error = f"{', '.join(missing_credentials)} {'is' if len(missing_credentials) == 1 else 'are'} required for --mode daytona"
            writer.log(f"Daytona credentials absent: {error}. Use --mode dry-run for artifact-only validation.")
            result = {
                "status": "failed",
                "mode": mode,
                "error": error,
                "cleanup": {"cleanup": "skipped"},
                "stale_check": {"active": [], "all": []},
            }
        else:
            result = _run_daytona(writer, archive_path, image=image or os.environ.get("TERMINAL_DAYTONA_IMAGE", DEFAULT_DAYTONA_IMAGE), tests=tests, run_id=run_id)

    cleanup_file = writer.write_json("cleanup.json", dict(result.get("cleanup", {})))
    stale_file = writer.write_json("stale-check.json", dict(result.get("stale_check", {})))

    manifest: dict[str, object] = {
        **result,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "host_repo": str(repo_root),
        "host_branch": _host_git(["branch", "--show-current"], repo_root),
        "host_head": _host_git(["rev-parse", "HEAD"], repo_root),
        "host_dirty": bool(_host_git(["status", "--short"], repo_root)),
        "daytona_api_url": os.environ.get("DAYTONA_API_URL"),
        "daytona_image": image or os.environ.get("TERMINAL_DAYTONA_IMAGE", DEFAULT_DAYTONA_IMAGE),
        "source_archive": str(archive_path),
        "commands": writer.commands,
        "commands_file": str(writer.commands_file),
        "transcript_file": str(writer.transcript_file),
        "cleanup_file": str(cleanup_file),
        "stale_check_file": str(stale_file),
    }
    writer.write_json("manifest.json", manifest)
    writer.log(f"Daytona real-repo smoke complete status={result.get('status')} manifest={run_dir / 'manifest.json'}")
    return 0 if result.get("status") == "passed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--run-id", default=utc_run_id())
    parser.add_argument("--mode", choices=["daytona", "dry-run"], default="daytona")
    parser.add_argument("--image", default=os.environ.get("TERMINAL_DAYTONA_IMAGE", DEFAULT_DAYTONA_IMAGE))
    parser.add_argument("--test", action="append", dest="tests", help="Remote test command to run after install; repeatable.")
    args = parser.parse_args(argv)

    run_dir = args.artifact_root / f"{args.run_id}-daytona-realrepo"
    return run_smoke(repo_root=args.repo_root.resolve(), run_dir=run_dir, mode=args.mode, run_id=args.run_id, tests=args.tests or DEFAULT_TESTS, image=args.image)


if __name__ == "__main__":
    raise SystemExit(main())
