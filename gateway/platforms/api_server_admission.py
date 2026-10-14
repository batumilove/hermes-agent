"""Bounded API turn admission and shutdown ownership accounting."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Dict, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from gateway.platforms.api_server import APIServerAdapter
from aiohttp import web

logger = logging.getLogger("gateway.platforms.api_server")
_api_agent_request_reservation = ContextVar("api_agent_request_reservation", default=None)

def _register_pending_api_work(adapter, *, detached: bool = False) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    reservation: dict[str, Any] = {
        "active": True,
        "detached": bool(detached),
        "request_id": request_id,
    }
    adapter._pending_agent_requests += 1
    adapter._pending_agent_request_ids.add(request_id)
    return reservation



class _TurnAdmission:
    """One API turn admission token, including queued wait metadata."""

    __slots__ = ("adapter", "released", "reservation")

    def __init__(self, adapter: APIServerAdapter, reservation: dict[str, bool]):
        self.adapter = adapter
        self.reservation = reservation
        self.released = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.release()
        return False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.reservation["active"] = False
        self.adapter._turn_admission_release()



class _QueuedTurnAdmission:
    """Context manager returned when a request is queued behind capacity."""

    __slots__ = ("adapter", "released", "reservation")

    def __init__(self, adapter: APIServerAdapter, reservation: dict[str, bool]):
        self.adapter = adapter
        self.reservation = reservation
        self.released = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.release()
        return False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.reservation["active"] = False
        self.adapter._turn_admission_release()



async def _acquire_turn_admission(adapter: APIServerAdapter) -> Optional[object]:
    """Wait for or reject an API turn admission slot."""
    limit = getattr(adapter, "_turn_admission_capacity", 0)
    if limit <= 0:
        return _TurnAdmission(adapter, {"active": True})

    queue_limit = getattr(adapter, "_turn_admission_queue_limit", 8)
    cond = adapter._turn_admission_condition
    async with cond:
        if adapter._turn_admission_active < limit:
            adapter._turn_admission_active += 1
            return _TurnAdmission(adapter, {"active": True})
        if adapter._turn_admission_queued >= queue_limit:
            return None
        adapter._turn_admission_queued += 1
        try:
            while adapter._turn_admission_active >= limit:
                await cond.wait()
            adapter._turn_admission_active += 1
        finally:
            adapter._turn_admission_queued = max(0, adapter._turn_admission_queued - 1)
        return _QueuedTurnAdmission(adapter, {"active": True})



def _admit_api_agent_request(handler=None, *, run_permission=None):
    """Own visible pending work and bounded FIFO admission before handler awaits."""
    if handler is None:
        return lambda callback: _admit_api_agent_request(callback, run_permission=run_permission)
    @wraps(handler)
    async def _wrapped(self, request, *args, **kwargs):
        from gateway.platforms.api_server import _openai_error
        auth_err = (self._check_run_auth(request, permission=run_permission)
                    if run_permission else self._check_auth(request))
        if auth_err:
            return auth_err
        draining = self._draining_response()
        if draining is not None:
            return draining
        reservation = _register_pending_api_work(self)
        context_token = _api_agent_request_reservation.set(reservation)
        disk_token = None
        admission = None
        try:
            try:
                from gateway import write_admission as gwa
                from hermes_constants import get_hermes_home
                disk_token = gwa.try_acquire_turn_admission(
                    get_hermes_home(), session_key=request.headers.get("X-Hermes-Session-Key") or None)
                if isinstance(disk_token, Exception):
                    response = gwa.admission_limit_response(disk_token)
                    disk_token = None
                    return web.json_response(response["body"], status=response["status"], headers=response["headers"])
            except (OSError, ValueError, TypeError):
                logger.debug("Turn admission unavailable; continuing", exc_info=True)
            admission = await _acquire_turn_admission(self)
            if admission is None:
                return web.json_response(
                    _openai_error("Too many concurrent runs waiting for admission.",
                                  err_type="rate_limit_error", code="rate_limit_exceeded"),
                    status=429, headers={"Retry-After": "1"})
            async with admission:
                if disk_token is not None:
                    async with disk_token:
                        return await handler(self, request, *args, **kwargs)
                return await handler(self, request, *args, **kwargs)
        finally:
            if disk_token is not None:
                disk_token.cancel()
                disk_token.release()
            _release_pending_api_work(self, reservation)
            _api_agent_request_reservation.reset(context_token)
    return _wrapped



def _release_pending_api_work(adapter, reservation: dict[str, Any]) -> None:
    """Release a pending-work reservation exactly once."""
    if reservation["active"]:
        reservation["active"] = False
        adapter._pending_agent_requests = max(0, adapter._pending_agent_requests - 1)
        adapter._pending_agent_request_ids.discard(reservation.get("request_id"))



@contextmanager
def _reserve_pending_api_work(adapter):
    """Keep externally-triggered background work visible across awaits.

    A handler can detach the reservation to an asyncio task; its done callback
    then owns release so shutdown cannot miss the handoff to background work.
    """
    reservation = _register_pending_api_work(adapter)
    try:
        yield reservation
    finally:
        if not reservation["detached"]:
            _release_pending_api_work(adapter, reservation)



class APIAdmissionMixin:
    def active_agent_work_snapshot(self) -> dict[str, Any]:
        """Return exact identities for the API work counted by shutdown drain."""
        units: list[dict[str, Any]] = []
        omissions: list[str] = []
        pending_count = max(0, int(getattr(self, "_pending_agent_requests", 0)))
        pending_ids = sorted(getattr(self, "_pending_agent_request_ids", set()))
        for request_id in pending_ids:
            units.append(
                {
                    "unit_id": f"api:request:{request_id}",
                    "category": "api",
                    "request_id": request_id,
                    "phase": "pending_agent_creation",
                    "work_class": "api_request_admission",
                    "drain_blocking": True,
                }
            )
        if len(pending_ids) != pending_count:
            omissions.append(
                f"api_pending_identity_mismatch:{len(pending_ids)}:{pending_count}"
            )

        inflight_count = max(0, int(getattr(self, "_inflight_agent_runs", 0)))
        inflight_agents = dict(getattr(self, "_shutdown_interruptible_agents", {}))
        for agent_key, agent in sorted(inflight_agents.items(), key=lambda item: item[0]):
            unit: dict[str, Any] = {
                "unit_id": f"api:agent:{agent_key}",
                "category": "api",
                "agent_key": str(agent_key),
                "phase": "active",
                "work_class": "api_turn",
                "drain_blocking": True,
            }
            session_id = getattr(agent, "session_id", None)
            if session_id:
                unit["session_id"] = str(session_id)
            units.append(unit)
        if len(inflight_agents) != inflight_count:
            omissions.append(
                f"api_inflight_identity_mismatch:{len(inflight_agents)}:{inflight_count}"
            )

        live_run_ids = sorted(
            str(run_id)
            for run_id, task in getattr(self, "_active_run_tasks", {}).items()
            if not task.done()
        )
        for run_id in live_run_ids:
            units.append(
                {
                    "unit_id": f"api:run:{run_id}",
                    "category": "api",
                    "run_id": run_id,
                    "phase": "active",
                    "work_class": "api_run",
                    "drain_blocking": True,
                }
            )
        count = pending_count + inflight_count + len(live_run_ids)
        if len(units) != count:
            omissions.append(f"api_unit_count_mismatch:{len(units)}:{count}")
        return {
            "count": count,
            "units": units,
            "attribution_complete": not omissions,
            "omissions": omissions,
        }


    @staticmethod
    def _resolve_turn_admission_capacity() -> int:
        """Read the live turn-admission capacity from config.yaml.

        Falls back to the concurrent-run cap, so a single knob keeps the live
        API gate and the legacy run-count limiter aligned unless explicitly
        overridden.
        """
        from gateway.platforms.api_server import APIServerAdapter
        default = APIServerAdapter._resolve_max_concurrent_runs()
        try:
            from hermes_cli.config import cfg_get, load_config

            raw = cfg_get(
                load_config(),
                "gateway",
                "api_server",
                "turn_admission_capacity",
                default=default,
            )
            value = int(raw)
        except (OSError, ValueError, TypeError):
            return default
        return max(0, value)


    @staticmethod
    def _resolve_turn_admission_queue_limit() -> int:
        """Read the bounded admission queue depth from config.yaml."""
        default = 8
        try:
            from hermes_cli.config import cfg_get, load_config

            raw = cfg_get(
                load_config(),
                "gateway",
                "api_server",
                "turn_admission_queue_limit",
                default=default,
            )
            value = int(raw)
        except (OSError, ValueError, TypeError):
            return default
        return max(0, value)


    def _turn_admission_release(self) -> None:
        """Wake the next queued request after a turn finishes."""
        self._turn_admission_active = max(0, self._turn_admission_active - 1)
        cond = getattr(self, "_turn_admission_condition", None)
        if cond is None:
            return

        async def _notify() -> None:
            async with cond:
                cond.notify_all()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(_notify())
        tasks = self.__dict__.setdefault("_turn_admission_notify_tasks", set())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
