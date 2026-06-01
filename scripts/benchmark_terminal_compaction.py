#!/usr/bin/env python3
"""Benchmark RTK terminal compaction against raw command output.

Runs each command normally, asks ``rtk rewrite`` for the RTK equivalent, runs the
rewritten command, and reports size/token savings plus overhead. It does not
modify Hermes config or install the RTK plugin.
"""
from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

DEFAULT_COMMANDS = [
    "git status --short",
    "git diff --stat",
    "git diff",
    "rg 'TODO|FIXME' .",
    "python -m pytest -q",
]


@dataclass
class Completed:
    returncode: int
    stdout: str
    stderr: str
    seconds: float

    @property
    def output(self) -> str:
        if self.stderr:
            return f"{self.stdout}{self.stderr}"
        return self.stdout


def estimate_tokens(text: str) -> int:
    """Return a rough token estimate using 4 chars/token, rounded up."""
    if not text:
        return 0
    return int(math.ceil(len(text) / 4))


def run_command(command: str | Sequence[str], *, cwd: str | None = None, timeout: int = 120) -> Completed:
    """Run a shell command string or argv sequence and capture combined output inputs."""
    start = time.perf_counter()
    proc = subprocess.run(
        command,
        shell=isinstance(command, str),
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
    return Completed(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        seconds=time.perf_counter() - start,
    )


def rewrite_command(command: str, rtk_binary: str = "rtk") -> str:
    """Return the RTK-rewritten command, or the original command on passthrough/failure."""
    try:
        result = run_command([rtk_binary, "rewrite", command], timeout=5)
    except Exception:
        return command

    if result.returncode not in {0, 3}:
        return command

    rewritten = result.stdout.strip()
    return rewritten or command


def compare_outputs(
    *,
    command: str,
    raw_output: str,
    rtk_output: str,
    raw_returncode: int,
    rtk_returncode: int,
    raw_seconds: float,
    rtk_seconds: float,
) -> dict[str, object]:
    raw_chars = len(raw_output)
    rtk_chars = len(rtk_output)
    saved_chars = raw_chars - rtk_chars
    savings_ratio = saved_chars / raw_chars if raw_chars else 0.0

    return {
        "command": command,
        "raw_chars": raw_chars,
        "rtk_chars": rtk_chars,
        "saved_chars": saved_chars,
        "savings_ratio": round(savings_ratio, 4),
        "raw_tokens_est": estimate_tokens(raw_output),
        "rtk_tokens_est": estimate_tokens(rtk_output),
        "raw_exit_code": raw_returncode,
        "rtk_exit_code": rtk_returncode,
        "exit_code_preserved": raw_returncode == rtk_returncode,
        "raw_seconds": round(raw_seconds, 4),
        "rtk_seconds": round(rtk_seconds, 4),
        "overhead_ms": round((rtk_seconds - raw_seconds) * 1000, 3),
    }


def benchmark_command(command: str, *, cwd: str | None, timeout: int, rtk_binary: str) -> dict[str, object]:
    raw = run_command(command, cwd=cwd, timeout=timeout)
    rewritten = rewrite_command(command, rtk_binary)
    rtk = run_command(rewritten, cwd=cwd, timeout=timeout)
    row = compare_outputs(
        command=command,
        raw_output=raw.output,
        rtk_output=rtk.output,
        raw_returncode=raw.returncode,
        rtk_returncode=rtk.returncode,
        raw_seconds=raw.seconds,
        rtk_seconds=rtk.seconds,
    )
    row["rewritten_command"] = rewritten
    row["was_rewritten"] = rewritten != command
    return row


def render_markdown(rows: list[dict[str, object]]) -> str:
    lines = ["# RTK terminal compaction benchmark", ""]
    for row in rows:
        savings_value = row["savings_ratio"]
        savings = (savings_value if isinstance(savings_value, (int, float)) else 0.0) * 100
        lines.extend(
            [
                f"## `{row['command']}`",
                f"- rewritten: `{row['rewritten_command']}`",
                f"- chars: {row['raw_chars']} raw → {row['rtk_chars']} rtk ({savings:.1f}% savings)",
                f"- tokens est: {row['raw_tokens_est']} raw → {row['rtk_tokens_est']} rtk",
                f"- exit code preserved: {row['exit_code_preserved']} ({row['raw_exit_code']} → {row['rtk_exit_code']})",
                f"- overhead: {row['overhead_ms']} ms",
                "",
            ]
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commands", nargs="*", help="Commands to benchmark. Defaults to a representative dev set.")
    parser.add_argument("--cwd", default=None, help="Working directory for benchmark commands")
    parser.add_argument("--timeout", type=int, default=120, help="Per-command timeout in seconds")
    parser.add_argument("--rtk", default="rtk", help="Path to RTK binary")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument("--output", help="Optional file path to write results")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    commands = args.commands or DEFAULT_COMMANDS

    rows = [benchmark_command(command, cwd=args.cwd, timeout=args.timeout, rtk_binary=args.rtk) for command in commands]
    rendered = json.dumps(rows, indent=2) if args.json else render_markdown(rows)

    if args.output:
        Path(args.output).write_text(rendered)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
