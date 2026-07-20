"""Authoritative verification and durable result records for ``hermes update``."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RemoteRefVerification:
    """Binding between a fetched remote-tracking ref and the live remote ref."""

    remote: str
    branch: str
    fetched_sha: str | None
    live_sha: str | None
    verified: bool
    error: str | None = None


@dataclass(frozen=True)
class UpstreamComparison:
    """Current official-upstream relation for the checked-out commit."""

    upstream_sha: str | None
    merged_through_sha: str | None
    behind: int | None
    carried: int | None
    verified: bool
    error: str | None = None


def _first_error_line(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text.splitlines()[0] if text else "git command failed"


def _is_object_id(value: str | None) -> bool:
    """Accept Git SHA-1/SHA-256 object IDs and reject other command output."""
    if value is None or len(value) not in {40, 64}:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value)


def fetch_and_verify_remote_ref(
    git_cmd: Sequence[str],
    cwd: Path,
    remote: str,
    branch: str,
) -> RemoteRefVerification:
    """Fetch ``remote/branch`` and bind the cached ref to ``git ls-remote``.

    A successful fetch alone is not accepted as proof: the resulting
    remote-tracking SHA must equal the branch SHA returned directly by the
    remote in the same verification cycle.
    """

    fetch = subprocess.run(
        [*git_cmd, "fetch", "--prune", remote, branch],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        return RemoteRefVerification(
            remote,
            branch,
            None,
            None,
            False,
            f"fetch failed: {_first_error_line(fetch)}",
        )

    tracking_ref = f"refs/remotes/{remote}/{branch}"
    parsed = subprocess.run(
        [*git_cmd, "rev-parse", tracking_ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if parsed.returncode != 0:
        return RemoteRefVerification(
            remote,
            branch,
            None,
            None,
            False,
            f"could not resolve {tracking_ref}: {_first_error_line(parsed)}",
        )
    fetched_sha = parsed.stdout.strip()
    if not _is_object_id(fetched_sha):
        return RemoteRefVerification(
            remote,
            branch,
            None,
            None,
            False,
            f"{tracking_ref} returned an invalid object ID",
        )

    live = subprocess.run(
        [*git_cmd, "ls-remote", remote, f"refs/heads/{branch}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if live.returncode != 0:
        return RemoteRefVerification(
            remote,
            branch,
            fetched_sha,
            None,
            False,
            f"ls-remote failed: {_first_error_line(live)}",
        )

    fields = live.stdout.strip().split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{branch}":
        return RemoteRefVerification(
            remote,
            branch,
            fetched_sha,
            None,
            False,
            f"remote branch refs/heads/{branch} was not returned",
        )
    live_sha = fields[0]
    if not _is_object_id(live_sha):
        return RemoteRefVerification(
            remote,
            branch,
            fetched_sha,
            None,
            False,
            f"refs/heads/{branch} returned an invalid object ID",
        )
    if fetched_sha != live_sha:
        return RemoteRefVerification(
            remote,
            branch,
            fetched_sha,
            live_sha,
            False,
            "fetched remote-tracking SHA does not match live remote SHA",
        )

    return RemoteRefVerification(
        remote,
        branch,
        fetched_sha,
        live_sha,
        True,
    )


def compare_with_upstream(
    git_cmd: Sequence[str],
    cwd: Path,
    remote: str = "upstream",
    branch: str = "main",
) -> UpstreamComparison:
    """Fetch official upstream and report the exact merge boundary and delta."""

    upstream = fetch_and_verify_remote_ref(git_cmd, cwd, remote, branch)
    if not upstream.verified:
        return UpstreamComparison(
            upstream.live_sha,
            None,
            None,
            None,
            False,
            upstream.error,
        )
    if not _is_object_id(upstream.live_sha):
        return UpstreamComparison(
            None,
            None,
            None,
            None,
            False,
            "official upstream returned an invalid object ID",
        )

    tracking_ref = f"{remote}/{branch}"
    merge_base = subprocess.run(
        [*git_cmd, "merge-base", tracking_ref, "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if merge_base.returncode != 0:
        return UpstreamComparison(
            upstream.live_sha,
            None,
            None,
            None,
            False,
            f"merge-base failed: {_first_error_line(merge_base)}",
        )
    merged_through_sha = merge_base.stdout.strip()
    if not _is_object_id(merged_through_sha):
        return UpstreamComparison(
            upstream.live_sha,
            None,
            None,
            None,
            False,
            "merge-base returned an invalid object ID",
        )

    divergence = subprocess.run(
        [*git_cmd, "rev-list", "--left-right", "--count", f"{tracking_ref}...HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if divergence.returncode != 0:
        return UpstreamComparison(
            upstream.live_sha,
            merged_through_sha,
            None,
            None,
            False,
            f"rev-list failed: {_first_error_line(divergence)}",
        )
    fields = divergence.stdout.strip().split()
    if len(fields) != 2:
        return UpstreamComparison(
            upstream.live_sha,
            merged_through_sha,
            None,
            None,
            False,
            "rev-list returned an invalid left/right count",
        )

    try:
        behind, carried = (int(fields[0]), int(fields[1]))
    except ValueError:
        return UpstreamComparison(
            upstream.live_sha,
            merged_through_sha,
            None,
            None,
            False,
            "rev-list returned non-integer left/right counts",
        )
    if behind < 0 or carried < 0:
        return UpstreamComparison(
            upstream.live_sha,
            merged_through_sha,
            None,
            None,
            False,
            "rev-list returned negative left/right counts",
        )

    return UpstreamComparison(
        upstream.live_sha,
        merged_through_sha,
        behind,
        carried,
        True,
    )


def write_update_result(home: Path, result: Mapping[str, Any]) -> Path:
    """Atomically replace the durable update result record under Hermes home."""

    home.mkdir(parents=True, exist_ok=True)
    path = home / ".update_result.json"
    payload = json.dumps(dict(result), indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=home,
            prefix=".update_result.json.tmp.",
            delete=False,
        ) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        try:
            directory_fd = os.open(home, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Directory fsync is unavailable on some platforms/filesystems.
            pass
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return path
