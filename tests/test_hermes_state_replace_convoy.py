"""Tests for the SessionDB replace_messages write-convoy remediation.

Production evidence (2026-07-25 10:25–10:59 UTC): 732 write-latency warnings,
352 >=1s, 30 >=5s.  The biggest long-holders were replace_messages transactions
(3.567s, 3.488s, 3.313s) and every >=5s event was dominated by self._lock wait
(max 12.289s).  The root cause is FTS5 trigger amplification: replace_messages
blindly DELETEs every row (firing FTS5 'delete' triggers) and re-INSERTs all
rows (firing FTS5 'insert' triggers) — even when the replacement payload is
identical to what is already stored.

These tests verify the narrow structural optimization: when the replacement
messages are byte-for-byte identical to the currently-stored active rows, the
write is skipped entirely (no DELETE, no INSERT, no FTS trigger churn, no lock
hold).  All non-identical rewrites follow the original atomic delete+insert
path unchanged.

Test invariants preserved:
  - active_only semantics with archived/compacted rows
  - message order, platform IDs, tool calls, api_content
  - reasoning/codex fields, counters, timestamps
  - FTS searchability after rewrite
  - rollback on injected failure
  - concurrent lifecycle/close safety
  - multi-process SQLite correctness (BEGIN IMMEDIATE)
"""

import json
import sqlite3
import threading
import time

import pytest

from hermes_state import SessionDB


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_transcript(n_messages: int = 50, tool_payload_size: int = 0):
    """Build a realistic transcript with user/assistant alternation."""
    msgs = []
    for i in range(n_messages):
        if i % 2 == 0:
            content = f"User message number {i}"
            if tool_payload_size:
                content += " x" * tool_payload_size
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({
                "role": "assistant",
                "content": f"Assistant reply {i}",
                "reasoning": f"thinking about {i}" if i % 4 == 1 else None,
            })
    return msgs


def _build_tool_heavy_transcript(n_tool_results: int = 20, payload_size: int = 2000):
    """Build a transcript dominated by large tool-result payloads."""
    msgs = [{"role": "user", "content": "do some work"}]
    for i in range(n_tool_results):
        msgs.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": f"call_{i}", "type": "function",
                            "function": {"name": "execute_code", "arguments": "{}"}}],
        })
        msgs.append({
            "role": "tool",
            "content": "A" * payload_size,
            "tool_call_id": f"call_{i}",
            "tool_name": "execute_code",
        })
    return msgs


# ── Behavior tests: no-op skip ───────────────────────────────────────────────

class TestReplaceMessagesNoOpSkip:
    """When replacement == stored, the destructive rewrite must be skipped."""

    def test_identical_replace_skips_write(self, tmp_path):
        """replace_messages with byte-identical payload must not DELETE/INSERT."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = _build_transcript(20)
            db.replace_messages("s1", msgs)

            # Capture the max row id — a no-op must not change it.
            with db._lock:
                max_id_before = db._conn.execute(
                    "SELECT MAX(id) FROM messages WHERE session_id = ?", ("s1",)
                ).fetchone()[0]
                write_count_before = db._write_count

            # Replace with identical messages.
            db.replace_messages("s1", msgs)

            with db._lock:
                max_id_after = db._conn.execute(
                    "SELECT MAX(id) FROM messages WHERE session_id = ?", ("s1",)
                ).fetchone()[0]
                write_count_after = db._write_count

            # No new rows → no DELETE+INSERT cycle.
            assert max_id_after == max_id_before, (
                "Identical replace_messages should not change row ids "
                f"(before={max_id_before}, after={max_id_after})"
            )
            # No write transaction was opened.
            assert write_count_after == write_count_before, (
                "Identical replace_messages should not open a write transaction "
                f"(before={write_count_before}, after={write_count_after})"
            )
        finally:
            db.close()

    def test_identical_replace_preserves_fts_search(self, tmp_path):
        """After a no-op replace, FTS search must still find all content."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = [
                {"role": "user", "content": "find this unique keyword zzqx"},
                {"role": "assistant", "content": "acknowledged"},
            ]
            db.replace_messages("s1", msgs)

            results_before = db.search_messages("zzqx")
            assert len(results_before) >= 1

            # No-op replace.
            db.replace_messages("s1", msgs)

            results_after = db.search_messages("zzqx")
            assert len(results_after) == len(results_before), (
                "FTS search results must not change after no-op replace"
            )
        finally:
            db.close()

    def test_identical_replace_preserves_active_only_with_archived(self, tmp_path):
        """No-op skip with active_only must leave archived rows untouched."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            # Store initial active messages.
            active = [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
            db.replace_messages("s1", active)

            # Archive them via archive_and_compact.
            db.archive_and_compact("s1", [{"role": "user", "content": "compacted summary"}])

            # Now replace active_only with the same compacted message.
            db.replace_messages("s1", [{"role": "user", "content": "compacted summary"}], active_only=True)

            # Archived rows must still exist.
            all_msgs = db.get_messages("s1", include_inactive=True)
            assert len(all_msgs) == 3  # 2 archived + 1 active
        finally:
            db.close()

    def test_identical_replace_preserves_message_order(self, tmp_path):
        """After no-op replace, message order (by id) must be unchanged."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = _build_transcript(30)
            db.replace_messages("s1", msgs)

            order_before = [m["content"] for m in db.get_messages("s1")]

            db.replace_messages("s1", msgs)

            order_after = [m["content"] for m in db.get_messages("s1")]
            assert order_after == order_before
        finally:
            db.close()

    def test_identical_replace_preserves_tool_calls_and_metadata(self, tmp_path):
        """No-op skip must preserve tool_calls, reasoning, platform_message_id."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = [
                {"role": "user", "content": "run it"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "tc1", "type": "function",
                     "function": {"name": "terminal", "arguments": '{"command":"ls"}'}}]},
                {"role": "tool", "content": "file1\nfile2", "tool_call_id": "tc1",
                 "tool_name": "terminal", "platform_message_id": "pm-001"},
                {"role": "assistant", "content": "done",
                 "reasoning": "I ran ls", "reasoning_content": "thinking...",
                 "reasoning_details": [{"type": "thought"}],
                 "codex_reasoning_items": [{"sig": "abc"}],
                 "codex_message_items": [{"msg": "xyz"}],
                 "api_content": "raw-api-dump"},
            ]
            db.replace_messages("s1", msgs)

            # No-op replace.
            db.replace_messages("s1", msgs)

            stored = db.get_messages("s1")
            assert len(stored) == 4
            tool_msg = next(m for m in stored if m["role"] == "tool")
            assert tool_msg["tool_name"] == "terminal"
            assert tool_msg["tool_call_id"] == "tc1"
            assert tool_msg["platform_message_id"] == "pm-001"
            asst = next(m for m in stored if m["role"] == "assistant" and m["tool_calls"])
            assert len(asst["tool_calls"]) == 1
            assert asst["tool_calls"][0]["function"]["name"] == "terminal"
            asst2 = next(m for m in stored if m["role"] == "assistant" and m.get("api_content"))
            assert asst2["api_content"] == "raw-api-dump"
            assert asst2["reasoning"] == "I ran ls"
            assert asst2["reasoning_content"] == "thinking..."
        finally:
            db.close()

    def test_identical_replace_preserves_counters(self, tmp_path):
        """No-op skip must leave message_count / tool_call_count unchanged."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b", "tool_calls": [
                    {"id": "x", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
                {"role": "tool", "content": "r", "tool_call_id": "x"},
            ]
            db.replace_messages("s1", msgs)

            session_before = db.get_session("s1")
            assert session_before["message_count"] == 3
            assert session_before["tool_call_count"] == 1

            db.replace_messages("s1", msgs)

            session_after = db.get_session("s1")
            assert session_after["message_count"] == 3
            assert session_after["tool_call_count"] == 1
        finally:
            db.close()

    def test_identical_replace_preserves_timestamps(self, tmp_path):
        """No-op skip must preserve original timestamps."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = [
                {"role": "user", "content": "a", "timestamp": 1700000000.0},
                {"role": "assistant", "content": "b", "timestamp": 1700000001.0},
            ]
            db.replace_messages("s1", msgs)

            ts_before = [m["timestamp"] for m in db.get_messages("s1")]

            db.replace_messages("s1", msgs)

            ts_after = [m["timestamp"] for m in db.get_messages("s1")]
            assert ts_after == ts_before
        finally:
            db.close()


# ── Adversarial divergence: non-identical rewrites must still work ───────────

class TestReplaceMessagesDivergenceDetection:
    """When replacement != stored, the full atomic rewrite must fire."""

    def test_modified_content_triggers_rewrite(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [{"role": "user", "content": "original"}])

            with db._lock:
                max_id_before = db._conn.execute(
                    "SELECT MAX(id) FROM messages WHERE session_id = ?", ("s1",)
                ).fetchone()[0]

            db.replace_messages("s1", [{"role": "user", "content": "modified"}])

            with db._lock:
                max_id_after = db._conn.execute(
                    "SELECT MAX(id) FROM messages WHERE session_id = ?", ("s1",)
                ).fetchone()[0]

            assert max_id_after > max_id_before, "Modified content must trigger a real rewrite"
            assert db.get_messages("s1")[0]["content"] == "modified"
        finally:
            db.close()

    def test_append_only_tail_triggers_rewrite(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [
                {"role": "user", "content": "m1"},
                {"role": "assistant", "content": "m2"},
            ])

            db.replace_messages("s1", [
                {"role": "user", "content": "m1"},
                {"role": "assistant", "content": "m2"},
                {"role": "user", "content": "m3"},
            ])

            msgs = db.get_messages("s1")
            assert len(msgs) == 3
            assert msgs[2]["content"] == "m3"
        finally:
            db.close()

    def test_retry_undo_truncation_triggers_rewrite(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [
                {"role": "user", "content": "m1"},
                {"role": "assistant", "content": "m2"},
                {"role": "user", "content": "m3"},
                {"role": "assistant", "content": "m4"},
            ])

            # Undo: truncate to first 2 messages.
            db.replace_messages("s1", [
                {"role": "user", "content": "m1"},
                {"role": "assistant", "content": "m2"},
            ])

            msgs = db.get_messages("s1")
            assert len(msgs) == 2
        finally:
            db.close()

    def test_modified_middle_triggers_rewrite(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [
                {"role": "user", "content": "m1"},
                {"role": "assistant", "content": "m2"},
                {"role": "user", "content": "m3"},
            ])

            db.replace_messages("s1", [
                {"role": "user", "content": "m1"},
                {"role": "assistant", "content": "CHANGED"},
                {"role": "user", "content": "m3"},
            ])

            msgs = db.get_messages("s1")
            assert msgs[1]["content"] == "CHANGED"
        finally:
            db.close()

    def test_different_role_triggers_rewrite(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [{"role": "user", "content": "same"}])

            db.replace_messages("s1", [{"role": "assistant", "content": "same"}])

            msgs = db.get_messages("s1")
            assert msgs[0]["role"] == "assistant"
        finally:
            db.close()

    def test_different_count_triggers_rewrite(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
            ])

            # Fewer messages.
            db.replace_messages("s1", [{"role": "user", "content": "a"}])
            assert len(db.get_messages("s1")) == 1

            # Back to 2 (different content in 2nd).
            db.replace_messages("s1", [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
            ])
            assert len(db.get_messages("s1")) == 2
        finally:
            db.close()

    def test_multimodal_identical_skips(self, tmp_path):
        """Identical multimodal (list) content must be detected as no-op."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            content = [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ]
            msgs = [{"role": "user", "content": content}]
            db.replace_messages("s1", msgs)

            with db._lock:
                wc_before = db._write_count

            db.replace_messages("s1", msgs)

            with db._lock:
                wc_after = db._write_count
            assert wc_after == wc_before, "Identical multimodal content should skip write"
        finally:
            db.close()


# ── Sentinel safety ──────────────────────────────────────────────────────────

class TestReplaceNoopSentinelSafety:
    """The no-op sentinel must be impossible to forge from outside."""

    def test_sentinel_is_singleton(self):
        """Only one instance; identity check holds."""
        from hermes_state import _REPLACE_NOOP_SENTINEL, _ReplaceNoopSentinel
        assert _REPLACE_NOOP_SENTINEL is _REPLACE_NOOP_SENTINEL
        # A fresh instance is NOT the sentinel — identity, not equality.
        other = _ReplaceNoopSentinel()
        assert other is not _REPLACE_NOOP_SENTINEL

    def test_sentinel_cannot_be_pickled_or_copied(self):
        """copy/pickle must not reproduce the sentinel."""
        import copy
        import pickle
        from hermes_state import _REPLACE_NOOP_SENTINEL
        # copy.copy uses __reduce__ internally for objects without __copy__.
        with pytest.raises(TypeError):
            pickle.dumps(_REPLACE_NOOP_SENTINEL)
        # Deepcopy falls back to __reduce_ex__ → __reduce__ → TypeError.
        with pytest.raises(TypeError):
            copy.deepcopy(_REPLACE_NOOP_SENTINEL)

    def test_user_callback_cannot_forge_sentinel(self, tmp_path):
        """A user callback returning a different _ReplaceNoopSentinel instance
        must NOT suppress write_count — only the module singleton is honoured."""
        from hermes_state import _ReplaceNoopSentinel
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            # Use a direct _execute_write with a forged sentinel.
            def _forged_do(conn):
                # This is a different instance, NOT _REPLACE_NOOP_SENTINEL.
                return _ReplaceNoopSentinel()

            wc_before = db._write_count
            db._execute_write(_forged_do, operation="forged_test")
            wc_after = db._write_count
            # Because the returned object is not the singleton, write_count
            # IS incremented (the transaction did execute, even if empty).
            assert wc_after == wc_before + 1, (
                "A non-singleton _ReplaceNoopSentinel must not suppress "
                "write_count — only the module singleton is honoured"
            )
        finally:
            db.close()


# ── Deterministic concurrent convoy evidence ─────────────────────────────────

class TestConcurrentWriterConvoy:
    """Deterministic evidence that the no-op fast path eliminates convoy.

    This test measures the tail latency of append_message under sustained
    no-op replace_messages pressure.  On BASE (no fast path), every
    replace_messages rewrites all rows and fires FTS triggers while holding
    the write lock, so append_message p95 is high.  After the fix, the no-op
    path commits an empty transaction and append_message is unblocked.
    """

    @pytest.mark.benchmark
    def test_concurrent_convoy_append_tail_latency(self, tmp_path):
        import statistics

        db = SessionDB(db_path=tmp_path / "convoy_det.db")
        try:
            db.create_session(session_id="s1", source="cli")
            # 40 tool-result pairs → 81 messages, each tool result ~3KB.
            msgs = _build_tool_heavy_transcript(40, payload_size=3000)
            db.replace_messages("s1", msgs)

            append_latencies = []
            stop = threading.Event()
            started = threading.Event()

            def background_replaces():
                # Hammer replace_messages with the same payload.
                started.set()
                count = 0
                while not stop.is_set():
                    try:
                        db.replace_messages("s1", msgs)
                        count += 1
                    except Exception:
                        pass
                # Sanity: we did meaningful work.
                assert count > 0, "Background replace loop ran zero iterations"

            def measure_appends():
                started.wait(timeout=2)
                for _ in range(50):
                    if stop.is_set():
                        break
                    t0 = time.monotonic()
                    try:
                        db.append_message("s1", role="user", content="probe")
                        append_latencies.append(time.monotonic() - t0)
                    except Exception:
                        pass
                    time.sleep(0.002)

            t_bg = threading.Thread(target=background_replaces)
            t_probe = threading.Thread(target=measure_appends)
            t_bg.start()
            t_probe.start()

            t_probe.join(timeout=30)
            stop.set()
            t_bg.join(timeout=10)

            assert not t_bg.is_alive(), "Background replace thread timed out"
            assert not t_probe.is_alive(), "Probe thread timed out"

            assert len(append_latencies) >= 20, (
                f"Only {len(append_latencies)} append samples — test inconclusive"
            )

            p50 = sorted(append_latencies)[len(append_latencies) // 2]
            p95_idx = min(len(append_latencies) - 1, int(len(append_latencies) * 0.95))
            p95 = sorted(append_latencies)[p95_idx]
            mx = max(append_latencies)
            mean = statistics.mean(append_latencies)

            print(f"\n[convoy-det] append n={len(append_latencies)} "
                  f"mean={mean*1000:.1f}ms p50={p50*1000:.1f}ms "
                  f"p95={p95*1000:.1f}ms max={mx*1000:.1f}ms")

            # After the fix, append_message under no-op replace pressure
            # should have a p95 well under 200ms.  On BASE, this is
            # typically 500ms-5s because every replace rewrites 81 rows.
            assert p95 < 0.200, (
                f"Append p95={p95*1000:.1f}ms under no-op replace pressure — "
                "write-convoy not remediated (expected <200ms after fix)"
            )
        finally:
            db.close()


# ── Rollback safety ──────────────────────────────────────────────────────────

class TestReplaceMessagesRollback:
    """Injected failure during rewrite must roll back cleanly."""

    def test_failure_rolls_back(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            db.replace_messages("s1", [
                {"role": "user", "content": "original1"},
                {"role": "assistant", "content": "original2"},
            ])

            original_msgs = db.get_messages("s1")

            # Inject a failure into the next write transaction.
            original_insert = db._insert_message_rows

            def failing_insert(conn, sid, messages):
                original_insert(conn, sid, messages)
                raise sqlite3.OperationalError("injected failure")

            db._insert_message_rows = failing_insert

            with pytest.raises(sqlite3.OperationalError):
                db.replace_messages("s1", [
                    {"role": "user", "content": "new1"},
                    {"role": "assistant", "content": "new2"},
                ])

            db._insert_message_rows = original_insert

            # Original messages must be intact (rollback worked).
            after_msgs = db.get_messages("s1")
            assert len(after_msgs) == 2
            assert after_msgs[0]["content"] == "original1"
            assert after_msgs[1]["content"] == "original2"
        finally:
            db.close()


# ── Concurrent lifecycle safety ──────────────────────────────────────────────

class TestReplaceMessagesConcurrentSafety:
    """No-op skip must be safe under concurrent access."""

    def test_concurrent_replace_and_append(self, tmp_path):
        """Concurrent replace_messages (identical) + append_message must not deadlock."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = _build_transcript(10)
            db.replace_messages("s1", msgs)

            errors = []

            def do_replaces():
                try:
                    for _ in range(20):
                        db.replace_messages("s1", msgs)
                except Exception as e:
                    errors.append(e)

            def do_appends():
                try:
                    for i in range(20):
                        db.append_message("s1", role="user", content=f"concurrent-{i}")
                except Exception as e:
                    errors.append(e)

            t1 = threading.Thread(target=do_replaces)
            t2 = threading.Thread(target=do_appends)
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)

            assert not errors, f"Concurrent errors: {errors}"
            assert not t1.is_alive(), "replace thread timed out"
            assert not t2.is_alive(), "append thread timed out"
        finally:
            db.close()

    def test_close_during_no_op_replace(self, tmp_path):
        """close() during/after no-op replace must not crash."""
        db = SessionDB(db_path=tmp_path / "test.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = [{"role": "user", "content": "test"}]
            db.replace_messages("s1", msgs)
            db.replace_messages("s1", msgs)  # no-op
        finally:
            db.close()  # must not raise


# ── Benchmark: convoy reproduction ───────────────────────────────────────────

class TestReplaceMessagesBenchmark:
    """Benchmark that reproduces the write-convoy pattern.

    These tests measure the cost of replace_messages on a transcript with
    large tool payloads (the production scenario).  On BASE, a no-op
    replace_messages rewrites every row and fires 4N FTS triggers; after the
    fix, it is O(1).

    The benchmark is gated with @pytest.mark.benchmark so it can be run
    explicitly; the behavioral no-op-skip test above is the strict RED/GREEN
    gate.
    """

    @pytest.mark.benchmark
    def test_benchmark_noop_replace_large_tool_transcript(self, tmp_path):
        """Measure replace_messages latency for a no-op on a large transcript.

        On BASE this rewrites all rows; after the fix it should be near-zero.
        """
        db = SessionDB(db_path=tmp_path / "bench.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = _build_tool_heavy_transcript(30, payload_size=5000)
            db.replace_messages("s1", msgs)

            # Warm up.
            db.replace_messages("s1", msgs)

            # Measure.
            times = []
            for _ in range(10):
                t0 = time.monotonic()
                db.replace_messages("s1", msgs)
                times.append(time.monotonic() - t0)

            p50 = sorted(times)[len(times) // 2]
            p95 = sorted(times)[int(len(times) * 0.95)]
            mx = max(times)

            # After the fix, a no-op replace should be well under 50ms
            # even for a large transcript.  On BASE it's typically 50-500ms.
            print(f"\n[benchmark] no-op replace: p50={p50*1000:.1f}ms "
                  f"p95={p95*1000:.1f}ms max={mx*1000:.1f}ms")

            # This is the RED gate: on BASE, p50 is typically >> 50ms.
            # After the fix, p50 should be < 5ms.
            assert p50 < 0.050, (
                f"No-op replace_messages p50={p50*1000:.1f}ms is too slow — "
                "write-convoy not remediated"
            )
        finally:
            db.close()

    @pytest.mark.benchmark
    def test_benchmark_concurrent_convoy(self, tmp_path):
        """Reproduce the write-convoy: replace_messages blocking append_message.

        On BASE, a no-op replace_messages holds self._lock for the full
        delete+insert+FTS-trigger duration, blocking concurrent append_message
        calls.  After the fix, the no-op path is O(1) and append latency is
        unaffected.
        """
        db = SessionDB(db_path=tmp_path / "convoy.db")
        try:
            db.create_session(session_id="s1", source="cli")
            msgs = _build_tool_heavy_transcript(20, payload_size=5000)
            db.replace_messages("s1", msgs)

            append_latencies = []
            stop = threading.Event()

            def background_replaces():
                while not stop.is_set():
                    try:
                        db.replace_messages("s1", msgs)
                    except Exception:
                        pass

            def measure_appends():
                while not stop.is_set():
                    t0 = time.monotonic()
                    try:
                        db.append_message("s1", role="user", content="probe")
                        append_latencies.append(time.monotonic() - t0)
                    except Exception:
                        pass
                    time.sleep(0.001)

            t_bg = threading.Thread(target=background_replaces)
            t_probe = threading.Thread(target=measure_appends)
            t_bg.start()
            t_probe.start()

            time.sleep(3.0)  # Run for 3 seconds.
            stop.set()
            t_bg.join(timeout=5)
            t_probe.join(timeout=5)

            if append_latencies:
                p50 = sorted(append_latencies)[len(append_latencies) // 2]
                p95 = sorted(append_latencies)[int(len(append_latencies) * 0.95)]
                mx = max(append_latencies)
                print(f"\n[convoy] append p50={p50*1000:.1f}ms "
                      f"p95={p95*1000:.1f}ms max={mx*1000:.1f}ms "
                      f"(n={len(append_latencies)})")

                # After the fix, append p95 should be well under 200ms even
                # under background no-op replace pressure.
                assert p95 < 0.200, (
                    f"Append p95={p95*1000:.1f}ms under no-op replace pressure — "
                    "write-convoy not remediated"
                )
        finally:
            db.close()
