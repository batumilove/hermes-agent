"""Cron output persistence and per-job archive retention."""

import logging
from pathlib import Path
from utils import atomic_write_text

logger = logging.getLogger("cron.jobs")


def _cron_output_keep() -> int:
    """Per-job output-file retention cap (``cron.output_retention``)."""
    return _jobs._cron_config_number("output_retention", _jobs._CRON_OUTPUT_DEFAULT_KEEP, int)


def _prune_job_output(job_output_dir: Path, keep: int) -> int:
    """Remove the oldest ``*.md`` run-output files beyond *keep*; returns count deleted. Filenames
    are timestamps, so a reverse lexical sort is newest-first. Non-positive *keep* disables pruning;
    failures are swallowed so they can never break output saving."""
    if keep <= 0:
        return 0
    try:
        files = sorted(
            (f for f in job_output_dir.glob("*.md") if f.is_file()),
            key=lambda f: f.name, reverse=True)
    except OSError:
        return 0
    deleted = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            deleted += 1
        except OSError as exc:
            logger.debug("Failed to prune cron output %s: %s", stale.name, exc)
    return deleted


def save_job_output(job_id: str, output: str):
    """Save job output to file."""
    _jobs.ensure_dirs()
    job_output_dir = _jobs._job_output_dir(job_id)
    _jobs._ensure_cron_dir(job_output_dir)
    _jobs._secure_dir(job_output_dir)
    output_file = job_output_dir / f"{_jobs._hermes_now().strftime('%Y-%m-%d_%H-%M-%S')}.md"
    atomic_write_text(output_file, output, tmp_prefix=".output_", mode=0o600)
    _jobs._secure_file(output_file)
    # Bound per-job output growth so long-running deploys don't fill the disk (#52383).
    _prune_job_output(job_output_dir, _cron_output_keep())
    return output_file


from cron import jobs as _jobs
