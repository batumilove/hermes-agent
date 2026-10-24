"""Fence delayed lifecycle status submissions before merging canonical state."""

from __future__ import annotations

import functools
import logging
import threading
import time
from pathlib import Path

from agent.async_utils import current_serial_daemon_sequence, latest_serial_daemon_sequence

logger = logging.getLogger(__name__)
_runtime_status_write_lock = threading.RLock()
_RUNTIME_STATUS_WRITE_LOCK_TIMEOUT_SECONDS = 10.0
_latest_serial_status_sequence = 0


def _serial_runtime_owner_is_valid() -> bool:
    from gateway import status

    from gateway.runtime_ownership import process_ownership
    home = status._get_process_hermes_home()
    return process_ownership.owns(home) or not status._get_gateway_lock_path().exists()


def release_runtime_lock(*, timeout=10.0) -> bool:
    from gateway import status

    deadline = None if timeout is None else time.monotonic() + max(float(timeout), 0.0)
    wait = -1 if deadline is None else max(deadline - time.monotonic(), 0.0)
    if not _runtime_status_write_lock.acquire(timeout=wait):
        return False
    try:
        writer = status._runtime_status_writer
        wait = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        if writer is not None and not writer.flush(timeout=wait):
            logger.warning("Runtime lock retained because admitted status writes did not settle")
            return False
        from gateway.runtime_ownership import process_ownership
        process_ownership.close()
        if process_ownership.homes:
            return False
        status._clear_running_pid_cache()
        return True
    finally:
        _runtime_status_write_lock.release()


def _superseded(sequence: int | None) -> bool:
    if sequence is None or sequence > _latest_serial_status_sequence:
        return False
    logger.warning(
        "Skipping superseded runtime-status write (sequence %s <= %s)",
        sequence,
        _latest_serial_status_sequence,
    )
    return True


def _serialize_runtime_status_write(func):
    """Fence queued work at submission, with bounded lock acquisition.

    The lock protects the canonical merge and enqueue only. Filesystem writes
    and persistence waits belong to the upstream coalescing writer outside it.
    Direct updates supersede every serial job already submitted, including a
    timed-out job that later reaches its status callback.
    """

    @functools.wraps(func)
    def _locked(*args, **kwargs):
        global _latest_serial_status_sequence
        sequence = current_serial_daemon_sequence()
        if _superseded(sequence):
            return None
        if not _runtime_status_write_lock.acquire(timeout=_RUNTIME_STATUS_WRITE_LOCK_TIMEOUT_SECONDS):
            raise TimeoutError("runtime-status submission lock unavailable")
        try:
            if _superseded(sequence):
                return None
            result = func(*args, **kwargs)
            applied = sequence if sequence is not None else latest_serial_daemon_sequence()
            _latest_serial_status_sequence = max(_latest_serial_status_sequence, applied)
            return result
        finally:
            _runtime_status_write_lock.release()

    return _locked


@_serialize_runtime_status_write
def submit_runtime_status_update(*, load_existing=True, reload_existing=False, **fields):
    from gateway import status

    if current_serial_daemon_sequence() is not None and not _serial_runtime_owner_is_valid():
        logger.warning("Skipping runtime-status write without current gateway lock ownership")
        return None
    with status._runtime_status_state_lock:
        path, payload, previous_payload = status._prepare_runtime_status_update(
            load_existing=load_existing, reload_existing=reload_existing, **fields
        )
        writer = status._get_runtime_status_writer()
        generation = writer.submit(path, payload)
    return writer, generation, previous_payload, payload


def write_runtime_status(*, reload_existing=False, wait_timeout=None, **fields):
    from gateway import status

    update = submit_runtime_status_update(reload_existing=reload_existing, **fields)
    if update is None:
        return True
    writer, generation, previous_payload, payload = update
    status._emit_runtime_status_transition(previous_payload, payload)
    return writer.wait(generation, timeout=wait_timeout)


def publish_runtime_status(**fields):
    from gateway import status

    update = submit_runtime_status_update(load_existing=False, **fields)
    if update is None:
        return 0
    _, generation, previous_payload, payload = update
    status._emit_runtime_status_transition(previous_payload, payload)
    return generation
