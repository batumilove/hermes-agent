"""Privacy-safe transport outcomes consumed by the existing delivery retry policy."""

import hashlib
import math


def delivery_failure_token(result) -> str:
    from gateway.dead_targets import classify_dead_error
    from gateway.delivery_ledger import flood_wait_seconds, is_flood_error, is_reconnect_only

    error = str(getattr(result, "error", "") or "final delivery incomplete")
    if is_reconnect_only(error):
        return "send_path_degraded"
    wait = getattr(result, "retry_after", None)
    if is_flood_error(error) or wait is not None:
        try:
            delay = float(wait) if wait is not None else flood_wait_seconds(error)
        except (ValueError, TypeError):
            delay = 60.0
        delay = min(max(delay, 1.0), 900.0) if math.isfinite(delay) else 60.0
        return f"flood_control:{delay:g}"
    dead = classify_dead_error(error)
    if dead:
        return "chat not found" if dead == "not_found" else "bot blocked"
    return "sha256:" + hashlib.sha256(error.encode("utf-8", "replace")).hexdigest()
