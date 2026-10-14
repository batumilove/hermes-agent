"""Hermetic state-backed agents for compression ownership regressions."""
from __future__ import annotations
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from hermes_state import SessionDB

def _build_agent_with_db(
    db: SessionDB,
    session_id: str,
    *,
    stub_compressor: bool = True,
):
    """Build an AIAgent that's wired to ``db`` and pinned to ``session_id``."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_db=db,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
            enabled_toolsets=["file"],
            cwd=str(Path(db.db_path).parent),
        )

    # Stub the compressor so it returns deterministic output and DOESN'T make
    # an LLM call.  Sleep inside compress() so the two threads' rotations
    # actually overlap — without that the OS could happen to serialize them
    # and hide the bug.
    if not stub_compressor:
        return agent

    compressor = MagicMock()

    def _compress_with_overlap(*_a, **_kw):
        time.sleep(0.15)
        return [
            {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
            {"role": "user", "content": "tail"},
        ]

    compressor.compress.side_effect = _compress_with_overlap
    compressor.compression_count = 1
    compressor.last_prompt_tokens = 0
    compressor.last_completion_tokens = 0
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    agent.context_compressor = compressor
    # The compressor is a stub — the one-time compression-model feasibility
    # probe would resolve a REAL auxiliary provider (credential pools, live
    # token exchanges over the network on some dev machines). That makes the
    # first _compress_context call in a test nondeterministically slow (>2s)
    # and flakes event-based timing assertions. Mark it done: these tests
    # exercise locking/fencing/rotation, never aux-model feasibility.
    agent._compression_feasibility_checked = True
    # These tests cover the ROTATION fallback path (forking, child sessions,
    # lock contention) — pin in_place=False so they keep exercising it
    # regardless of the global default (which flipped to True in #38763).
    agent.compression_in_place = False
    return agent


import pytest


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch):
    """Compression locking tests use a fixed model window without live catalogs."""
    monkeypatch.setattr("agent.model_metadata.get_model_context_length", lambda *_a, **_kw: 128_000)
    monkeypatch.setattr("agent.context_compressor.get_model_context_length", lambda *_a, **_kw: 128_000)
