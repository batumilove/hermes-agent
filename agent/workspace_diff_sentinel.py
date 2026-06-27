"""Workspace diff sentinel — advisory broad-mutation detector.

This is separate from the file-mutation verifier: the verifier tracks failed
write_file/patch claims; the sentinel snapshots workspace dirt before/after a
turn and reports only new broad deltas. It is advisory-only.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Default thresholds (must match hermes_cli/config.py DEFAULT_CONFIG).
_DEFAULT_MAX_CHANGED_FILES = 5
_DEFAULT_MAX_INSERTIONS = 500
_DEFAULT_MAX_DELETIONS = 500
_DEFAULT_MAX_TOTAL_LINES = 800
_DEFAULT_INCLUDE_STAT_LINES = 20

# Safety cap for untracked file line counting (avoid reading multi-GB files).
_UNTRACKED_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class WorkspaceDiffSnapshot:
    root: Path
    entries: dict[str, dict[str, Any]]
    stat_lines: list[str]
    total_insertions: int
    total_deletions: int
    total_changed_files: int


def _get_sentinel_config() -> dict[str, Any]:
    """Read the ``display.workspace_diff_sentinel`` dict from config.yaml.

    Returns an empty dict on any failure so callers can fall back to defaults.
    """
    try:
        from hermes_cli.config import load_config as _load_config
        _cfg = _load_config() or {}
    except Exception:
        return {}
    _display = _cfg.get("display") if isinstance(_cfg, dict) else None
    if isinstance(_display, dict):
        _sentinel = _display.get("workspace_diff_sentinel")
        if isinstance(_sentinel, dict):
            return _sentinel
    return {}


def _sentinel_enabled() -> bool:
    """Return True if the workspace-diff sentinel is enabled (default: True).

    Reads ``display.workspace_diff_sentinel.enabled`` from config.yaml.
    Returns True if the key is absent or config is unreadable.
    """
    _sentinel = _get_sentinel_config()
    if "enabled" in _sentinel:
        return bool(_sentinel["enabled"])
    return True  # default: enabled


def _count_untracked_lines(path: Path) -> tuple[int, int]:
    """Return (insertions, bytes) for an untracked file, capped and binary-safe.

    - Skips binary files (checks for null bytes in first 8KB).
    - Caps read at _UNTRACKED_MAX_BYTES.
    - Returns (line_count, byte_count) or (0, 0) on any error / binary.
    """
    try:
        st = path.stat()
        size = st.st_size
        if size == 0:
            return (0, 0)
        # Binary check: read first 8KB and look for null bytes.
        with path.open("rb") as fh:
            sample = fh.read(8192)
        if b"\x00" in sample:
            return (0, 0)
        # Cap read size.
        read_size = min(size, _UNTRACKED_MAX_BYTES)
        with path.open("rb") as fh:
            data = fh.read(read_size)
        # Count lines; if file doesn't end with newline, last line still counts.
        line_count = data.count(b"\n")
        if data and not data.endswith(b"\n"):
            line_count += 1
        return (line_count, len(data))
    except Exception:
        return (0, 0)


def compute_workspace_diff_snapshot(cwd: Path | str) -> Optional[WorkspaceDiffSnapshot]:
    root = Path(cwd)
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return None
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=git_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        numstat = subprocess.run(
            ["git", "diff", "--numstat", "--no-renames"],
            cwd=git_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        cached_numstat = subprocess.run(
            ["git", "diff", "--cached", "--numstat", "--no-renames"],
            cwd=git_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except Exception:
        return None
    entries: dict[str, dict[str, Any]] = {}
    for line in status:
        if not line:
            continue
        code = line[:2]
        path = line[3:] if len(line) > 3 else line
        entries[path] = {"status": code.strip() or "?", "insertions": 0, "deletions": 0}
    total_insertions = total_deletions = 0
    for line in numstat:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dels, path = parts
        if path in entries:
            entries[path]["insertions"] = int(ins) if ins.isdigit() else 0
            entries[path]["deletions"] = int(dels) if dels.isdigit() else 0
        total_insertions += int(ins) if ins.isdigit() else 0
        total_deletions += int(dels) if dels.isdigit() else 0
    # Staged changes are invisible to git diff --numstat (unstaged only).
    for line in cached_numstat:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dels, path = parts
        if path in entries:
            entries[path]["insertions"] = int(ins) if ins.isdigit() else 0
            entries[path]["deletions"] = int(dels) if dels.isdigit() else 0
        total_insertions += int(ins) if ins.isdigit() else 0
        total_deletions += int(dels) if dels.isdigit() else 0
    # Untracked files are invisible to git diff --numstat; count them manually.
    for path, info in entries.items():
        if info["status"] == "??":
            _ins, _bytes = _count_untracked_lines(Path(git_root) / path)
            info["insertions"] = _ins
            # Use byte count as a proxy for deletions (0) + insertions weight
            # so thresholds catch huge untracked files even if line count is low.
            info["untracked_bytes"] = _bytes
            total_insertions += _ins
    _sentinel = _get_sentinel_config()
    _include_stat_lines = _sentinel.get("include_stat_lines", _DEFAULT_INCLUDE_STAT_LINES)
    return WorkspaceDiffSnapshot(
        root=Path(git_root),
        entries=entries,
        stat_lines=status[:_include_stat_lines],
        total_insertions=total_insertions,
        total_deletions=total_deletions,
        total_changed_files=len(entries),
    )


def build_workspace_diff_footer(before: WorkspaceDiffSnapshot | None, after: WorkspaceDiffSnapshot | None) -> str:
    if before is None or after is None or before.root != after.root:
        return ""
    new_entries = {k: v for k, v in after.entries.items() if k not in before.entries or before.entries[k] != v}
    if not new_entries:
        return ""
    changed_files = len(new_entries)
    ins = sum(v.get("insertions", 0) for v in new_entries.values())
    dels = sum(v.get("deletions", 0) for v in new_entries.values())
    # Add untracked byte weight to total lines so huge binary-ish text files
    # (e.g. minified JS, logs) that have few lines but massive bytes still trip
    # thresholds. 1 byte ≈ 1 "line" for threshold math only.
    untracked_bytes = sum(v.get("untracked_bytes", 0) for v in new_entries.values())
    total_lines = ins + dels + untracked_bytes

    # Read thresholds from config, falling back to defaults.
    _sentinel = _get_sentinel_config()
    max_changed_files = _sentinel.get("max_changed_files", _DEFAULT_MAX_CHANGED_FILES)
    max_insertions = _sentinel.get("max_insertions", _DEFAULT_MAX_INSERTIONS)
    max_deletions = _sentinel.get("max_deletions", _DEFAULT_MAX_DELETIONS)
    max_total_lines = _sentinel.get("max_total_lines", _DEFAULT_MAX_TOTAL_LINES)
    include_stat_lines = _sentinel.get("include_stat_lines", _DEFAULT_INCLUDE_STAT_LINES)

    if changed_files <= max_changed_files and ins <= max_insertions and dels <= max_deletions and total_lines <= max_total_lines:
        return ""
    lines = [
        "Advisory: broad workspace mutation detected.",
        f"{changed_files} file(s) changed; +{ins} / -{dels}.",
    ]
    # Build footer detail lines from the *new* changed entry set, not the whole repo,
    # so triggering files are always included even when many preexisting dirty files exist.
    detail_entries = sorted(new_entries.keys())
    for path in detail_entries[:include_stat_lines]:
        info = new_entries[path]
        status = info.get("status", "?")
        lines.append(f"• {status:2} {path}")
    return "\n".join(lines)