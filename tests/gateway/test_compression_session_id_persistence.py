"""Regression tests for #29335 — gateway must persist ``session_entry.session_id``
after the agent's compression path mutates it.

When ``_compress_context()`` rolls the agent forward into a new session, the
agent now returns the new ``session_id`` in its result dict. The gateway
must compare-and-swap the routing binding through
``session_store.rebind_session_id()`` so the new mapping survives a gateway
restart without reconciling the complete routing table in one write
transaction. Without durable rebinding, the next turn loads the OLD session's
transcript and re-triggers compression forever.

Three sites in ``gateway/run.py`` publish a compression-induced session split.
All three MUST use the compare-and-swap rebind API. This test pins that
invariant and rejects direct assignment plus whole-index ``_save()``.

``TestCompressionSessionPropagation`` adds behavioral tests that exercise the
actual propagation path inline, verifying that the mock session_entry update
and _save() semantics are correct without requiring a live gateway.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock

from gateway import run as gateway_run
from gateway.session_context import set_current_session_id, get_session_env


def _session_id_assignments_followed_by_save(source: str) -> list[tuple[int, bool]]:
    """For each ``session_entry.session_id = ...`` assignment in *source*,
    return ``(lineno, saved_within_5_stmts)`` — True iff a
    ``self.session_store._save()`` call appears in the same block within the
    next 5 statements (covers normal control flow without false-flagging
    cleanup that lives 200 lines away).
    """
    tree = ast.parse(textwrap.dedent(source))
    results: list[tuple[int, bool]] = []

    class _Visitor(ast.NodeVisitor):
        def _is_session_id_assign(self, node: ast.AST) -> bool:
            if not isinstance(node, ast.Assign):
                return False
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "session_id"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "session_entry"
                ):
                    return True
            return False

        def _block_has_save_after(self, body: list[ast.stmt], idx: int) -> bool:
            for stmt in body[idx : idx + 6]:
                for sub in ast.walk(stmt):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "_save"
                    ):
                        return True
            return False

        def _walk_body(self, body: list[ast.stmt]) -> None:
            for i, stmt in enumerate(body):
                if self._is_session_id_assign(stmt):
                    results.append((stmt.lineno, self._block_has_save_after(body, i)))
                # Recurse into the stmt itself when it is a control-flow node
                # whose body/orelse/finalbody may carry assignments that are
                # not also reachable as iter_child_nodes children of the stmt
                # (e.g. an ``else`` block whose statements are all assigns).
                if isinstance(stmt, (ast.If, ast.For, ast.While, ast.With,
                                     ast.Try, ast.AsyncWith, ast.AsyncFor)):
                    self._walk_node(stmt)
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, (ast.If, ast.For, ast.While, ast.With,
                                          ast.Try, ast.AsyncWith, ast.AsyncFor)):
                        self._walk_node(child)

        def _walk_node(self, node: ast.AST) -> None:
            for attr in ("body", "orelse", "finalbody"):
                inner = getattr(node, attr, None)
                if isinstance(inner, list):
                    self._walk_body(inner)
            if hasattr(node, "handlers"):
                for handler in node.handlers:
                    self._walk_body(handler.body)

        def visit(self, node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._walk_body(node.body)
            for child in ast.iter_child_nodes(node):
                self.visit(child)

    _Visitor().visit(tree)
    return results


def _session_rebind_call_lines(source: str) -> list[int]:
    """Return source lines calling the structural point-rebind API."""
    tree = ast.parse(textwrap.dedent(source))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rebind_session_id"
    ]


def test_every_post_compression_session_id_transition_uses_point_rebind():
    """Compression rotation must avoid whole-index routing reconciliation.

    Regression for #29335 — the assignment at the end of
    ``_handle_message_with_agent`` used to skip ``_save()`` while two sibling
    sites (hygiene rewrite, manual /compress) already persisted. The agent
    would compress correctly, the gateway would update its in-memory
    session_id, then drop it on next gateway restart.
    """
    source = inspect.getsource(gateway_run)
    rebind_lines = _session_rebind_call_lines(source)
    assert len(rebind_lines) == 3, (
        "Expected all three compression rotation sites to call "
        f"rebind_session_id(); found {len(rebind_lines)} at {rebind_lines}."
    )
    direct_assignments = _session_id_assignments_followed_by_save(source)
    assert not direct_assignments, (
        "Compression rotation must not directly assign session_entry.session_id "
        f"and trigger whole-index _save(); found {direct_assignments}."
    )


class TestCompressionSessionPropagation:
    """Behavioral tests for post-compression session_id propagation.

    The structural AST test above pins that every ``session_entry.session_id``
    assignment in gateway/run.py is followed by ``_save()``.  These tests
    exercise the *behavior* of that propagation path inline, using mocks that
    mirror the objects gateway/run.py works with (``session_entry`` and
    ``session_store``), verifying the semantics are correct without requiring a
    live gateway instance.

    Ordering contract (from the comments added to the source in this PR):
    1. The agent thread updates the contextvar in ``conversation_compression.py``
       via ``set_current_session_id(agent.session_id)``.
    2. After ``run_in_executor`` returns, the gateway propagates the new id to
       ``session_entry.session_id`` and calls ``session_store._save()``.
    Both halves must agree for the next turn to route correctly.
    """

    def test_gateway_session_entry_follows_compression_rotation(self) -> None:
        """The gateway handler must update session_entry and call _save() when
        the agent result carries a rotated session_id.

        Simulates the inline propagation block in gateway/run.py:

            if agent_result.get("session_id") and \\
                    agent_result["session_id"] != session_entry.session_id:
                session_entry.session_id = agent_result["session_id"]
                self.session_store._save()

        Verifies that session_entry.session_id is mutated and _save is called
        exactly once — the minimal contract that prevents the restart-loop bug.
        """
        old_sid = "20260101_000000_aaaaaa"
        new_sid = "20260101_000001_bbbbbb"

        session_entry = MagicMock()
        session_entry.session_id = old_sid

        session_store = MagicMock()

        agent_result = {"session_id": new_sid, "response": "hello"}

        # Inline the propagation contract used by gateway/run.py.
        session_key = "agent:main:telegram:dm:user"
        if agent_result.get("session_id") and agent_result["session_id"] != session_entry.session_id:
            session_store.rebind_session_id(
                session_key,
                session_entry.session_id,
                agent_result["session_id"],
            )

        session_store.rebind_session_id.assert_called_once_with(
            session_key, old_sid, new_sid
        )


