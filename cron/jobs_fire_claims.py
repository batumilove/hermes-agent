"""Manual trigger consumption and exact-owner fire claim rollback."""

from typing import Any


def _validate_schedule_repeat_invariant(
    schedule: dict[str, Any],
    repeat: int | dict[str, Any] | None,
) -> None:
    """Reject an impossible repeat budget on a one-shot schedule.

    A ``once`` schedule has only one eligible occurrence. Storing a larger
    repeat count is misleading: the scheduler completes the job after that
    occurrence and never consumes the remaining budget.
    """
    if schedule.get("kind") != "once":
        return
    times = repeat.get("times") if isinstance(repeat, dict) else repeat
    if times is not None and times > 1:
        raise ValueError(
            "one-shot schedules run exactly once; use an interval schedule "
            "such as 'every 10m' for repeated jobs."
        )


def clear_pending_trigger(job_id: str, expected_execution_id: str) -> bool:
    """Consume only the manual trigger whose audit row was adopted by this run."""
    def apply(jobs, _i, job):
        marker = job.get("pending_trigger")
        if not isinstance(marker, dict) or marker.get("execution_id") != expected_execution_id:
            return False
        job.pop("pending_trigger", None)
        _jobs.save_jobs(jobs)
        return True
    return _jobs._with_job(job_id, apply, False)


def release_fire_claim(job_id: str, *, expected_owner: str) -> bool:
    """Re-arm the exact claimed slot if audit persistence failed before dispatch."""
    def apply(jobs, _i, job):
        claim = job.get("fire_claim")
        if not isinstance(claim, dict) or claim.get("by") != expected_owner:
            return False
        if "scheduled_for" in claim:
            job["next_run_at"] = claim["scheduled_for"]
        job["fire_claim"] = None
        _jobs.save_jobs(jobs)
        return True
    return _jobs._with_job(job_id, apply, False)


from cron import jobs as _jobs
