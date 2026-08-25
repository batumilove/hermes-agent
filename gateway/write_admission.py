"""Gateway-wide write admission for agent-serving endpoints (P0-A part 2).

Live evidence (2026-08-19 06:00-06:22Z, gateway MainPID 3141597): write-heavy
runs from 137 SessionDB instances convoyed on one state.db WAL write lock
with only per-instance jitter retry between them. The persistence layer now
bounds this via ``hermes_state.SessionDBWriteAdmission`` (keyed by db path);
this module gives the gateway's agent-serving endpoints the front door:

* ``try_acquire_turn_admission`` — non-blocking admission for a whole agent
  turn's write-heavy work. Returns a token (release in ``finally``), or the
  ``SessionDBWriteAdmissionFullError`` exception so the endpoint can answer
  HTTP 429 + ``Retry-After`` without spawning any agent work.
* ``get_admission_for_profile`` — the per-profile TURN controller shared by
  every endpoint serving the same profile home. Deliberately a SEPARATE
  controller from ``hermes_state``'s per-path disk-admission registry: a
  turn must not consume a disk-write slot while the model streams (it holds
  no write lock then), so the two bounds are sized and released
  independently — one turn valve per profile, one disk admission per
  state.db file.
* ``admission_limit_response`` — one serialization of queue-full as a 429
  JSON envelope with ``Retry-After`` for the platforms that want it.

Design rules honored here:

* FIFO fairness and per-session ordering live in the shared controller, not
  per-endpoint; endpoints only register intent and release.
* Slot release on all exit paths is the CALLER's contract via the token's
  context manager (``with token:`` …) or try/finally ``release()``.
* Drain-aware: at gateway shutdown the shared controller is ``shutdown()``;
  queued turn admissions fail fast with ``SessionDBWriteAdmissionClosedError``
  so drain is not held hostage by queued work that will never run.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_state import (
    SessionDBWriteAdmission,
    SessionDBWriteAdmissionFullError,
)

__all__ = [
    "SessionDBWriteAdmissionFullError",
    "get_admission_for_profile",
    "try_acquire_turn_admission",
    "try_acquire_turn_admission_or_raise",
    "admission_limit_response",
    "reset_gateway_admissions_for_tests",
]

# profile-home → shared controller. Normally identical to the hermes_state
# per-path controllers (keyed by <profile>/state.db); this map exists so a
# profile home with a NON-default state.db name/config still gets exactly one
# controller per profile, and so tests can exercise the gateway front door
# without touching real profile dirs.
_PROFILE_ADMISSIONS: "Dict[str, SessionDBWriteAdmission]" = {}
_PROFILE_ADMISSIONS_LOCK = threading.Lock()

# Turn admission bounds: the turn-level queue is the one callers see as 429.
# Generous compared to the per-transaction admission inside hermes_state — a
# turn holds no write slot while the model streams, only while it writes, so
# queuing more turns than transactions is safe (they multiplex inside).
_TURN_ADMISSION_CAPACITY = 8
_TURN_ADMISSION_QUEUE_LIMIT = 64


def _profile_key(profile_home: Any) -> str:
    return str(Path(str(profile_home)).resolve())


def get_admission_for_profile(profile_home: Any) -> SessionDBWriteAdmission:
    """The one shared turn-admission controller for a profile home."""
    key = _profile_key(profile_home)
    with _PROFILE_ADMISSIONS_LOCK:
        admission = _PROFILE_ADMISSIONS.get(key)
        if admission is None:
            admission = SessionDBWriteAdmission(
                capacity=_TURN_ADMISSION_CAPACITY,
                queue_limit=_TURN_ADMISSION_QUEUE_LIMIT,
            )
            _PROFILE_ADMISSIONS[key] = admission
        return admission


def try_acquire_turn_admission(
    profile_home: Any, session_key: Optional[str] = None
):
    """Try to admit one agent turn. Never blocks, never raises queue-full.

    Returns the admission token on success, or the
    ``SessionDBWriteAdmissionFullError`` instance when the queue is full
    (map to 429 + Retry-After via ``admission_limit_response``), or ``None``
    only when the controller is shut down mid-drain (treat as
    retry-later/503, not an agent-visible error). The token's slot is
    released on all exit paths via its context manager or ``release()``.
    """
    admission = get_admission_for_profile(profile_home)
    try:
        return admission.acquire(session_key=session_key)
    except SessionDBWriteAdmissionFullError as exc:
        return exc
    except Exception:
        # Closed mid-drain or any unexpected controller state: admission is
        # backpressure, not correctness — fail open so a draining gateway
        # still serves already-queued work without inventing a new failure
        # mode. (Per-turn persistence admission inside hermes_state is the
        # correctness boundary for disk writes.)
        return None


def try_acquire_turn_admission_or_raise(
    profile_home: Any, session_key: Optional[str] = None
):
    """Variant that raises on queue-full (for tests and sync paths)."""
    admission = get_admission_for_profile(profile_home)
    return admission.acquire(session_key=session_key)


def admission_limit_response(full_error: SessionDBWriteAdmissionFullError) -> Dict[str, Any]:
    """Serialize queue-full as an OpenAI-style 429 envelope + Retry-After."""
    retry_after = max(1, int(getattr(full_error, "retry_after_s", 1.0) or 1.0))
    return {
        "status": 429,
        "headers": {"Retry-After": str(retry_after)},
        "body": {
            "error": {
                "message": (
                    "Server busy: state.db write admission queue is full "
                    f"(queue_depth={full_error.queue_depth}); retry after "
                    f"{retry_after}s"
                ),
                "type": "rate_limit_error",
                "code": "state_db_write_admission_full",
            }
        },
    }


def reset_gateway_admissions_for_tests() -> None:
    """Drop all per-profile controllers (tests only)."""
    with _PROFILE_ADMISSIONS_LOCK:
        _PROFILE_ADMISSIONS.clear()
