"""Close-once ownership for delegated workers, including abandoned turns."""
from __future__ import annotations
import logging
import threading
from agent.interrupt_compat import request_hard_interrupt
logger = logging.getLogger("tools.delegate_tool")

def _install_deferred_child_close_gate(child) -> None:
    """Install a close-once lifecycle lease before a child becomes visible."""
    if getattr(child, "_delegate_close_gate_installed", False) is True:
        return

    close_lock = threading.Lock()
    close_state = {"active": 0, "requested": False, "closed": False}

    def _close_child_once() -> None:
        try:
            close_child = getattr(child, "close", None)
            if callable(close_child):
                close_child()
        except Exception:
            logger.debug("Failed to close child agent after delegation", exc_info=True)

    def _request_close() -> None:
        close_now = False
        with close_lock:
            close_state["requested"] = True
            if close_state["active"] == 0 and not close_state["closed"]:
                close_state["closed"] = True
                close_now = True
        if close_now:
            _close_child_once()

    def _begin_use() -> bool:
        with close_lock:
            if close_state["requested"] or close_state["closed"]:
                return False
            close_state["active"] += 1
            return True

    def _end_use() -> None:
        close_now = False
        with close_lock:
            if close_state["active"] <= 0:
                return
            close_state["active"] -= 1
            if (
                close_state["active"] == 0
                and close_state["requested"]
                and not close_state["closed"]
            ):
                close_state["closed"] = True
                close_now = True
        if close_now:
            _close_child_once()

    child._delegate_request_close = _request_close
    child._delegate_begin_use = _begin_use
    child._delegate_end_use = _end_use
    child._delegate_close_gate_installed = True

def _retire_child_without_owner(child, parent_agent, *, interrupt_reason=None) -> None:
    """Retire a built child when no worker owns its normal finally path."""
    _install_deferred_child_close_gate(child)
    if interrupt_reason:
        try:
            request_hard_interrupt(
                child,
                interrupt_reason,
                tool_reason="subagent lifecycle retired before completion",
            )
        except Exception:
            logger.debug("Failed to interrupt retired child", exc_info=True)

    request_close = getattr(child, "_delegate_request_close", None)
    if callable(request_close):
        request_close()
    else:
        try:
            child.close()
        except Exception:
            logger.debug("Failed to close retired child", exc_info=True)

    if parent_agent is not None and hasattr(parent_agent, "_active_children"):
        try:
            lock = getattr(parent_agent, "_active_children_lock", None)
            if lock:
                with lock:
                    parent_agent._active_children.remove(child)
            else:
                parent_agent._active_children.remove(child)
        except (ValueError, AttributeError):
            pass
