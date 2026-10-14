"""Emit ``hermes.compaction`` records as NeMo Relay marks on the owning session's scope stack.

The mark goes under the live turn of the same session when there is one, else under that session's
scope (gateway hygiene and other out-of-turn compactions), else under the live turn the compacting agent
started in (a child session a rotating commit created mid-turn), and never from a turn Relay does not
instrument. The session's own stack matters: Relay resets LLM-history freshness only for the agent scope
that owns a ``compaction`` mark, so the next LLM start after a committed rewrite records the full history
again. No live Relay host or session scope means
there is nothing to record. Relay exporters are configured by the user, so the mark adds no outbound
path of its own.
"""

from __future__ import annotations

from contextlib import nullcontext
import contextvars
import logging
from typing import Any

from agent import relay_runtime

logger = logging.getLogger(__name__)

COMPACTION_DATA_SCHEMA = {"name": "hermes.compaction", "version": "1"}
# Marks are observability: after one stalled publish, stop waiting on Relay for the rest of the process
# so a wedged pipeline costs one bounded timeout, not one per compaction under the compression lease.
_stalled = False
_Target = tuple[relay_runtime.RelayRuntime, relay_runtime.RelaySession, Any, Any, relay_runtime.RelayTurnContext | None]


def _turn_target(session_id: str) -> _Target | None:
    turn = relay_runtime.active_turn(session_id)
    host = turn.lease.live_runtime() if turn is not None else None
    if host is None:
        return None
    session = turn.lease.session
    with turn.logical_llm_lock:
        context = turn.idle_logical_llm_contexts.get("primary")
        if context is None:
            context = next((value for key, value in turn.logical_llm_contexts.items()
                            if turn.logical_llm_roles.get(key) == "primary"), turn.context)
    return host, session, turn.handle or session.handle, context, turn


def _session_target(session_id: str) -> _Target | None:
    host = relay_runtime.HOST_REGISTRY.for_profile(create=False)
    if not isinstance(host, relay_runtime.RelayRuntime):
        return None
    session = host.get_session(session_id)
    if session is None or session.handle is None:
        return None
    return host, session, session.handle, None, None


def _resolve_target(
    session_id: str, turn_session_id: str | None = None,
) -> _Target | None:
    """``(host, session, parent handle, native context, turn)`` for ``session_id``: its live turn, then
    its session scope, then the live turn of ``turn_session_id``.

    ``turn_session_id`` is the session the compacting agent's turn started in. A rotating commit moves the
    agent to a child session that has no Relay scope until the next turn opens one, so a later rotation in
    the same turn is marked on that turn. When the child already has a scope, that scope owns the next LLM
    starts for the child and keeps the mark. Concurrent turns have independent native contexts, so a
    compaction marks the owning turn without refreshing another turn's history."""
    if not relay_runtime.relay_instrumentation_enabled():
        return None
    target = _turn_target(session_id) or _session_target(session_id)
    if target is None and turn_session_id:
        target = _turn_target(turn_session_id)
    return target


def compaction_target_available(session_id: str, *, turn_session_id: str | None = None) -> bool:
    """Whether a mark for this session has a live Relay scope to receive it."""
    return bool(session_id and _resolve_target(session_id, turn_session_id))


def emit_compaction_mark(
    session_id: str, name: str, data: dict[str, Any], *, turn_session_id: str | None = None,
) -> bool:
    """Emit one mark; returns False when no Relay scope owns ``session_id`` (see ``_resolve_target``)."""
    target = _resolve_target(session_id, turn_session_id) if session_id else None
    if target is None:
        logger.debug("no live Relay session for %s; %s mark not recorded", session_id or "none", name)
        return False
    global _stalled
    if _stalled:
        return False
    try:
        return _publish_target(target, name, data)
    except TimeoutError:
        _stalled = True
        raise


def _publish_target(target: _Target, name: str, data: dict[str, Any]) -> bool:
    turn = target[4]
    # Match logical-call acquisition order, and keep selection/publish/cache eligibility atomic.
    with turn.finalize_lock if turn is not None else nullcontext():
        with turn.logical_llm_lock if turn is not None else nullcontext():
            if turn is not None:
                target = _turn_target(turn.lease.session_id)
                if turn.closed or target is None:
                    return False
            host, session, handle, context, _turn = target
            host.run_in_session(
                session, host.relay.scope.event, name, handle=handle, data=data,
                data_schema=dict(COMPACTION_DATA_SCHEMA), metadata=relay_runtime.runtime_metadata(host.runtime_id),
                timeout=relay_runtime.SCOPE_OP_TIMEOUT, scope_context=_event_context(context),
            )
            if turn is not None and name == "compaction":
                _discard_unmarked_primary_contexts(turn, context)
    return True


def _discard_unmarked_primary_contexts(turn: relay_runtime.RelayTurnContext, marked_context: Any) -> None:
    # A concurrent call's unmarked stack must not later replace the refreshed stack in the cache.
    for request_id, context in turn.logical_llm_contexts.items():
        if turn.logical_llm_roles.get(request_id) == "primary" and context is not marked_context:
            turn.non_reusable_logical_llm_calls.add(request_id)


def _event_context(native_context: Any) -> contextvars.Context | None:
    if native_context is None:
        return None
    context = contextvars.copy_context()
    for variable, value in native_context.items():
        context.run(variable.set, value)
    return context
