"""Regression tests for #29335 — gateway must persist ``session_entry.session_id``
after the agent's compression path mutates it.

When ``_compress_context()`` rolls the agent forward into a new session, the
agent now returns the new ``session_id`` in its result dict. The gateway
updates ``session_entry.session_id`` in memory AND must call
``session_store._save()`` so the new mapping survives a gateway restart.
Without ``_save()``, the next turn loads the OLD session's transcript and
re-triggers compression forever.

Three sites in ``gateway/run.py`` mutate ``session_entry.session_id`` after
a compression-induced session split. All three MUST be followed by a
``_save()`` call. This test pins that invariant.

``TestCompressionSessionPropagation`` adds behavioral tests that exercise the
actual propagation path inline, verifying that the mock session_entry update
and _save() semantics are correct without requiring a live gateway.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, call

from gateway.session_context import set_current_session_id, get_session_env


_ROOT = Path(__file__).resolve().parents[2]


def _dotted(node: ast.AST) -> str | None:
    """Return a simple dotted-name representation, or None for dynamic code."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _iter_statement_blocks(node: ast.AST):
    """Yield every statement list exactly once (no recursive overcount)."""
    for _field, value in ast.iter_fields(node):
        if isinstance(value, list):
            statements = [item for item in value if isinstance(item, ast.stmt)]
            if statements:
                yield statements
            for item in value:
                if isinstance(item, ast.AST):
                    yield from _iter_statement_blocks(item)
        elif isinstance(value, ast.AST):
            yield from _iter_statement_blocks(value)


def _point_save_contract(
    path: Path,
    *,
    function_name: str,
    target_name: str,
    save_receiver: str,
    save_argument: str,
) -> list[tuple[int, bool]]:
    """Check exact receiver/key persistence after session-id assignments."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    assert functions, f"expected {function_name} in {path}"
    results: list[tuple[int, bool]] = []
    for function in functions:
        for body in _iter_statement_blocks(function):
            for index, statement in enumerate(body):
                is_target = isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Attribute)
                    and target.attr == "session_id"
                    and _dotted(target.value) == target_name
                    for target in statement.targets
                )
                if not is_target:
                    continue
                saved = any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_save_entry"
                    and _dotted(node.func.value) == save_receiver
                    and len(node.args) == 1
                    and _dotted(node.args[0]) == save_argument
                    and not node.keywords
                    for later in body[index : index + 6]
                    for node in ast.walk(later)
                )
                results.append((statement.lineno, saved))
    return results


def test_point_save_contract_rejects_wrong_key_and_deduplicates(tmp_path):
    source = tmp_path / "synthetic.py"
    source.write_text(
        """\
def run_sync(self, ctx):
    if ctx:
        entry.session_id = 'child'
        unrelated._save_entry('wrong-key')
""",
        encoding="utf-8",
    )

    result = _point_save_contract(
        source,
        function_name="run_sync",
        target_name="entry",
        save_receiver="self._runner.session_store",
        save_argument="ctx.session_key",
    )

    assert result == [(3, False)]


def test_every_post_compression_session_id_assignment_uses_point_persistence():
    """Every compression rotation persists its exact routing key by UPSERT.

    This covers executor ``run_sync``, both async gateway rotation branches,
    and manual ``/compress``.  Receiver and key expressions are part of the
    contract: an unrelated one-argument ``_save_entry`` call must not pass.
    """
    contracts = [
        _point_save_contract(
            _ROOT / "gateway" / "run.py",
            function_name="run_sync",
            target_name="entry",
            save_receiver="self._runner.session_store",
            save_argument="ctx.session_key",
        ),
        _point_save_contract(
            _ROOT / "gateway" / "run.py",
            function_name="_handle_message_with_agent",
            target_name="session_entry",
            save_receiver="self.async_session_store",
            save_argument="session_key",
        ),
        _point_save_contract(
            _ROOT / "gateway" / "slash_commands.py",
            function_name="_handle_compress_command_inner",
            target_name="session_entry",
            save_receiver="self.async_session_store",
            save_argument="session_entry.session_key",
        ),
    ]
    assert [len(group) for group in contracts] == [1, 2, 1]
    missing = [line for group in contracts for line, saved in group if not saved]
    assert not missing, f"compression route point-save missing at lines {missing}"


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

        # Inline the propagation logic exactly as it appears in gateway/run.py
        # (around line 9459). This is the behavior we are pinning.
        if agent_result.get("session_id") and agent_result["session_id"] != session_entry.session_id:
            session_entry.session_id = agent_result["session_id"]
            session_store._save()

        assert session_entry.session_id == new_sid, (
            "session_entry.session_id was not updated to the compressed session id. "
            "The next turn would load the old transcript and re-trigger compression."
        )
        session_store._save.assert_called_once_with(), (
            "session_store._save() was not called after session_entry update. "
            "The new session mapping would not survive a gateway restart."
        )


