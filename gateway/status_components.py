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
