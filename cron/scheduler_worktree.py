"""Throttled background worktree maintenance for cron ticks."""

import contextlib
import contextvars
import logging
from pathlib import Path
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger("cron.scheduler")


# Worktree prune throttle: the cron tick is the only reliably periodic process on gateway boxes.
_WORKTREE_MAINTENANCE_INTERVAL_SECONDS = 6 * 3600.0
_last_worktree_maintenance_at: Optional[dict] = None  # profile home -> monotonic stamp
_worktree_maintenance_lock = threading.Lock()


def _worktree_maintenance_repos() -> list[str]:
    """Repos whose ``.worktrees/`` to keep pruned: the hermes checkout, job workdir repo roots and
    every repo this profile created a worktree in (``worktree_registry``), filtered to those that
    actually have a ``.worktrees/`` dir."""
    repos: set = set()

    with contextlib.suppress(Exception):
        from hermes_cli.worktree_registry import registered_repos
        repos.update(registered_repos())

    # Hermes source checkout (git installs only; wheel installs have no .git).
    with contextlib.suppress(Exception):
        install_root = Path(__file__).resolve().parent.parent
        if (install_root / ".git").exists():
            repos.add(str(install_root))

    with contextlib.suppress(Exception):
        from cron.jobs import load_jobs

        for job in load_jobs():
            workdir = str(job.get("workdir") or "").strip()
            if not workdir or not Path(workdir).is_dir():
                continue
            try:
                probe = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=5, cwd=workdir, check=False)
                if probe.returncode == 0 and probe.stdout.strip():
                    repos.add(probe.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                logger.debug("Cannot resolve cron worktree repository", exc_info=True)

    return [r for r in sorted(repos) if (Path(r) / ".worktrees").is_dir()]


def _maybe_run_worktree_maintenance() -> None:
    """Throttled worktree prune from the cron tick, on a daemon thread so the tick never waits on
    git. Same pruner as ``hermes -w`` startup (idle dirty/unpushed trees archived before removal,
    live trees untouched). Throttled per profile home: a multiplexed tick runs once per served
    profile, and each must sweep its own registered repos under its own config. The thread runs in
    a copy of the caller's context so the profile-home override travels with it. Errors never
    propagate: GC is hygiene, not scheduling."""
    global _last_worktree_maintenance_at
    import contextvars

    from hermes_constants import get_hermes_home

    home = str(get_hermes_home())
    now = time.monotonic()
    with _worktree_maintenance_lock:
        stamps = _last_worktree_maintenance_at or {}
        last = stamps.get(home)
        if last is not None and now - last < _WORKTREE_MAINTENANCE_INTERVAL_SECONDS:
            return
        _last_worktree_maintenance_at = {**stamps, home: now}

    def _run() -> None:
        try:
            repos = _worktree_maintenance_repos()
            if not repos:
                return
            from hermes_cli.worktree_ops import _prune_stale_worktrees

            for repo in repos:
                try:
                    _prune_stale_worktrees(repo)
                except Exception:
                    logger.debug("Cron worktree maintenance failed for %s", repo, exc_info=True)
        except Exception:
            logger.debug("Cron worktree maintenance skipped", exc_info=True)

    threading.Thread(target=contextvars.copy_context().run, args=(_run,), name="cron-worktree-prune",
                     daemon=True).start()


