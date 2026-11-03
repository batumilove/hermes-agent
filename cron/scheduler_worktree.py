"""Throttled background worktree maintenance for cron ticks."""

import contextlib
import contextvars
import logging
from pathlib import Path
import subprocess
import threading
import time

logger = logging.getLogger("cron.scheduler")


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
    live trees untouched). Discovery is throttled per profile home; pruning is serialized and
    throttled per repo across served profiles. The thread copies the caller's profile context.
    Errors never propagate: GC is hygiene, not scheduling."""
    from cron import scheduler
    from hermes_constants import get_hermes_home

    home = str(get_hermes_home())
    now = time.monotonic()
    with scheduler._worktree_maintenance_lock:
        stamps = scheduler._last_worktree_maintenance_at or {}
        last = stamps.get(home)
        if last is not None and now - last < scheduler._WORKTREE_MAINTENANCE_INTERVAL_SECONDS:
            return
        scheduler._last_worktree_maintenance_at = {**stamps, home: now}

    def _run() -> None:
        from cron.scheduler_worktree_maintenance import run_sweep

        run_sweep(scheduler._worktree_maintenance_repos,
                  interval_seconds=scheduler._WORKTREE_MAINTENANCE_INTERVAL_SECONDS)

    threading.Thread(target=contextvars.copy_context().run, args=(_run,), name="cron-worktree-prune",
                     daemon=True).start()


