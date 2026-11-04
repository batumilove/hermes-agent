"""Attribute session storage and scheduler health to their reporting process."""


def apply_component_status(payload, current_record, *, session_store, scheduler):
    from gateway import status

    provenance = {
        "writer_pid": current_record["pid"],
        "writer_start_time": current_record["start_time"],
    }
    if session_store is not status._UNSET:
        payload["session_store"] = {**status._coerce_session_store(session_store), **provenance}
    if scheduler is not status._UNSET:
        value = scheduler if isinstance(scheduler, dict) else {}
        state = str(value.get("status") or "unknown")
        if state not in {"running", "stopping", "failed", "unknown"}:
            state = "unknown"
        payload["scheduler"] = {
            "status": state,
            "provider": str(value.get("provider") or "unknown"),
            **provenance,
        }


def publish_housekeeping_status(cron_thread, cron_provider, stop_event):
    """Refresh scheduler liveness with the heartbeat instead of inheriting an old PID."""
    from gateway.run import _write_runtime_status_quiet

    if cron_thread is None:
        _write_runtime_status_quiet()
        return
    state = "stopping" if stop_event.is_set() else (
        "running" if cron_thread.is_alive() else "failed"
    )
    _write_runtime_status_quiet(scheduler={
        "status": state,
        "provider": getattr(cron_provider, "name", "unknown"),
    })
