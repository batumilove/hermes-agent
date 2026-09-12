from __future__ import annotations

from cron.scheduler import (
    bind_running_job_execution,
    get_running_job_attribution,
    release_running_job,
    try_register_running_job,
)


def test_running_job_attribution_binds_execution_for_full_registered_lifetime():
    job_id = "drain-attribution-job"
    unrelated_job_id = "unrelated-running-job"
    release_running_job(job_id)
    release_running_job(unrelated_job_id)
    assert try_register_running_job(unrelated_job_id) is True
    assert try_register_running_job(job_id) is True
    try:
        bind_running_job_execution(job_id, "execution-123")
        records = tuple(
            record
            for record in get_running_job_attribution()
            if record["job_id"] == job_id
        )
        assert records == (
            {
                "job_id": job_id,
                "execution_id": "execution-123",
                "phase": "running",
            },
        )
    finally:
        release_running_job(job_id)
        release_running_job(unrelated_job_id)

    assert all(
        record["job_id"] != job_id for record in get_running_job_attribution()
    )
