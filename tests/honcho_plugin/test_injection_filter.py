"""Tests for injection_filter — reason labels and stale/task-progress suppression.

These tests verify:
1. MemoryChunk dataclass holds text, reason, source, and staleness metadata
2. classify_memory() assigns reason labels based on content patterns
3. filter_stale_memories() suppresses stale/contradicted/task-progress items
4. Reason labels survive the annotation/formatting step
5. Stable preferences and active-project context survive filtering
"""

import pytest

from plugins.memory.honcho.injection_filter import (
    MemoryChunk,
    REASON_EXACT_ENTITY_MATCH,
    REASON_SEMANTIC_MATCH,
    REASON_RECENT_CORRECTION,
    REASON_STABLE_USER_PREFERENCE,
    REASON_ACTIVE_PROJECT_MATCH,
    REASON_SESSION_CONTINUITY,
    REASON_UNKNOWN,
    REASON_TASK_PROGRESS,
    REASON_CONTRADICTED,
    REASON_STALE,
    classify_memory,
    filter_stale_memories,
    annotate_chunk,
    annotate_chunks,
)


# ---------------------------------------------------------------------------
# MemoryChunk dataclass
# ---------------------------------------------------------------------------


class TestMemoryChunk:
    def test_minimal_construction(self):
        chunk = MemoryChunk(text="User prefers dark mode")
        assert chunk.text == "User prefers dark mode"
        assert chunk.reason == REASON_UNKNOWN
        assert chunk.source == ""
        assert chunk.is_stale is False
        assert chunk.metadata == {}

    def test_full_construction(self):
        chunk = MemoryChunk(
            text="User prefers dark mode",
            reason=REASON_STABLE_USER_PREFERENCE,
            source="base_context",
            is_stale=False,
            metadata={"peer": "user"},
        )
        assert chunk.text == "User prefers dark mode"
        assert chunk.reason == REASON_STABLE_USER_PREFERENCE
        assert chunk.source == "base_context"
        assert chunk.is_stale is False
        assert chunk.metadata == {"peer": "user"}

    def test_is_immutable_or_has_equality(self):
        """MemoryChunk should support equality comparison for test assertions."""
        a = MemoryChunk(text="hello", reason=REASON_UNKNOWN)
        b = MemoryChunk(text="hello", reason=REASON_UNKNOWN)
        assert a == b

    def test_different_reasons_not_equal(self):
        a = MemoryChunk(text="hello", reason=REASON_UNKNOWN)
        b = MemoryChunk(text="hello", reason=REASON_STABLE_USER_PREFERENCE)
        assert a != b


# ---------------------------------------------------------------------------
# Reason label constants
# ---------------------------------------------------------------------------


class TestReasonConstants:
    def test_reasons_are_strings(self):
        for reason in [
            REASON_EXACT_ENTITY_MATCH,
            REASON_SEMANTIC_MATCH,
            REASON_RECENT_CORRECTION,
            REASON_STABLE_USER_PREFERENCE,
            REASON_ACTIVE_PROJECT_MATCH,
            REASON_SESSION_CONTINUITY,
            REASON_UNKNOWN,
            REASON_TASK_PROGRESS,
            REASON_CONTRADICTED,
            REASON_STALE,
        ]:
            assert isinstance(reason, str)
            assert len(reason) > 0

    def test_reasons_are_unique(self):
        reasons = [
            REASON_EXACT_ENTITY_MATCH,
            REASON_SEMANTIC_MATCH,
            REASON_RECENT_CORRECTION,
            REASON_STABLE_USER_PREFERENCE,
            REASON_ACTIVE_PROJECT_MATCH,
            REASON_SESSION_CONTINUITY,
            REASON_UNKNOWN,
            REASON_TASK_PROGRESS,
            REASON_CONTRADICTED,
            REASON_STALE,
        ]
        assert len(reasons) == len(set(reasons))


# ---------------------------------------------------------------------------
# classify_memory — reason assignment
# ---------------------------------------------------------------------------


class TestClassifyMemory:
    def test_stable_user_preference(self):
        text = "User prefers concise responses"
        reason = classify_memory(text)
        assert reason == REASON_STABLE_USER_PREFERENCE

    def test_stable_preference_case_insensitive(self):
        text = "User Prefers Dark Mode"
        reason = classify_memory(text)
        assert reason == REASON_STABLE_USER_PREFERENCE

    def test_active_project_match(self):
        text = "Project uses pytest with xdist for parallel testing"
        reason = classify_memory(text)
        assert reason == REASON_ACTIVE_PROJECT_MATCH

    def test_active_project_case_insensitive(self):
        text = "The project builds on React and TypeScript"
        reason = classify_memory(text)
        assert reason == REASON_ACTIVE_PROJECT_MATCH

    def test_task_progress_with_pr_number(self):
        text = "Fixed bug X and submitted PR #123"
        reason = classify_memory(text)
        assert reason == REASON_TASK_PROGRESS

    def test_task_progress_with_commit(self):
        text = "Phase 2 done, commit abc1234"
        reason = classify_memory(text)
        assert reason == REASON_TASK_PROGRESS

    def test_task_progress_with_issue(self):
        text = "Closed issue #456 after fixing the login bug"
        reason = classify_memory(text)
        assert reason == REASON_TASK_PROGRESS

    def test_session_continuity(self):
        text = "In the previous session we discussed migrating the database"
        reason = classify_memory(text)
        assert reason == REASON_SESSION_CONTINUITY

    def test_recent_correction(self):
        text = "Corrected: user timezone is PST, not EST"
        reason = classify_memory(text)
        assert reason == REASON_RECENT_CORRECTION

    def test_correction_variant_updated(self):
        text = "Updated: user now uses venv instead of conda"
        reason = classify_memory(text)
        assert reason == REASON_RECENT_CORRECTION

    def test_unknown_for_generic_text(self):
        text = "The weather is nice today"
        reason = classify_memory(text)
        assert reason == REASON_UNKNOWN

    def test_stable_preference_takes_priority_over_project(self):
        """When text mentions both a preference and a project, preference wins."""
        text = "User prefers Python for this project"
        reason = classify_memory(text)
        assert reason == REASON_STABLE_USER_PREFERENCE

    def test_task_progress_takes_priority_over_session_continuity(self):
        """Task-progress patterns override session-continuity when both match."""
        text = "In the previous session, we completed Phase 3 and merged PR #789"
        reason = classify_memory(text)
        assert reason == REASON_TASK_PROGRESS


# ---------------------------------------------------------------------------
# filter_stale_memories — suppression
# ---------------------------------------------------------------------------


class TestFilterStaleMemories:
    def test_empty_list_returns_empty(self):
        result = filter_stale_memories([])
        assert result == []

    def test_non_stale_preferred_passes_through(self):
        chunks = [
            MemoryChunk(text="User prefers dark mode", reason=REASON_STABLE_USER_PREFERENCE),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 1
        assert result[0].reason == REASON_STABLE_USER_PREFERENCE

    def test_task_progress_suppressed(self):
        chunks = [
            MemoryChunk(text="Fixed bug X and submitted PR #123", reason=REASON_TASK_PROGRESS),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 0

    def test_stale_suppressed(self):
        chunks = [
            MemoryChunk(text="Old config value", reason=REASON_STALE),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 0

    def test_contradicted_suppressed(self):
        chunks = [
            MemoryChunk(text="User timezone is EST", reason=REASON_CONTRADICTED),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 0

    def test_stable_preference_survives_alongside_task_progress(self):
        """A stable preference should survive even when task-progress is also present."""
        chunks = [
            MemoryChunk(text="User prefers concise responses", reason=REASON_STABLE_USER_PREFERENCE),
            MemoryChunk(text="Fixed bug X and submitted PR #123", reason=REASON_TASK_PROGRESS),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 1
        assert result[0].reason == REASON_STABLE_USER_PREFERENCE
        assert "concise" in result[0].text

    def test_active_project_survives_filtering(self):
        chunks = [
            MemoryChunk(text="Project uses pytest with xdist", reason=REASON_ACTIVE_PROJECT_MATCH),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 1
        assert result[0].reason == REASON_ACTIVE_PROJECT_MATCH

    def test_correction_suppresses_contradicted(self):
        """When a correction and a contradicted fact exist, the correction survives."""
        chunks = [
            MemoryChunk(text="User timezone is EST", reason=REASON_CONTRADICTED),
            MemoryChunk(text="Corrected: user timezone is PST, not EST", reason=REASON_RECENT_CORRECTION),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 1
        assert result[0].reason == REASON_RECENT_CORRECTION

    def test_explicit_stale_flag_suppresses(self):
        """Chunks with is_stale=True are always suppressed regardless of reason."""
        chunks = [
            MemoryChunk(
                text="User prefers dark mode",
                reason=REASON_STABLE_USER_PREFERENCE,
                is_stale=True,
            ),
        ]
        result = filter_stale_memories(chunks)
        assert len(result) == 0

    def test_mixed_filtering(self):
        """Complex scenario with multiple chunk types."""
        chunks = [
            MemoryChunk(text="User prefers concise responses", reason=REASON_STABLE_USER_PREFERENCE),
            MemoryChunk(text="Phase 2 done, commit abc123", reason=REASON_TASK_PROGRESS),
            MemoryChunk(text="Project uses pytest", reason=REASON_ACTIVE_PROJECT_MATCH),
            MemoryChunk(text="Old API endpoint", reason=REASON_STALE),
            MemoryChunk(text="Corrected: use v2 API", reason=REASON_RECENT_CORRECTION),
        ]
        result = filter_stale_memories(chunks)
        reasons = [c.reason for c in result]
        assert REASON_STABLE_USER_PREFERENCE in reasons
        assert REASON_ACTIVE_PROJECT_MATCH in reasons
        assert REASON_RECENT_CORRECTION in reasons
        assert REASON_TASK_PROGRESS not in reasons
        assert REASON_STALE not in reasons
        assert len(result) == 3


# ---------------------------------------------------------------------------
# annotate_chunk / annotate_chunks — reason label formatting
# ---------------------------------------------------------------------------


class TestAnnotateChunk:
    def test_single_chunk_annotation(self):
        chunk = MemoryChunk(
            text="User prefers dark mode",
            reason=REASON_STABLE_USER_PREFERENCE,
            source="base_context",
        )
        annotated = annotate_chunk(chunk)
        assert "User prefers dark mode" in annotated
        assert REASON_STABLE_USER_PREFERENCE in annotated
        assert "base_context" in annotated

    def test_annotation_format_is_compact(self):
        chunk = MemoryChunk(
            text="hello",
            reason=REASON_UNKNOWN,
            source="dialectic",
        )
        annotated = annotate_chunk(chunk)
        # Should be a single line prefix, not multi-line
        assert "\n" not in annotated.split("hello")[0]

    def test_empty_source_omitted(self):
        chunk = MemoryChunk(text="hello", reason=REASON_UNKNOWN, source="")
        annotated = annotate_chunk(chunk)
        assert "hello" in annotated
        # Source should not appear when empty
        assert annotated.count("source") == 0 or "source=" not in annotated.lower()


class TestAnnotateChunks:
    def test_multiple_chunks_joined(self):
        chunks = [
            MemoryChunk(text="User prefers dark mode", reason=REASON_STABLE_USER_PREFERENCE),
            MemoryChunk(text="Project uses pytest", reason=REASON_ACTIVE_PROJECT_MATCH),
        ]
        result = annotate_chunks(chunks)
        assert "User prefers dark mode" in result
        assert "Project uses pytest" in result
        assert REASON_STABLE_USER_PREFERENCE in result
        assert REASON_ACTIVE_PROJECT_MATCH in result

    def test_empty_list_returns_empty(self):
        result = annotate_chunks([])
        assert result == ""

    def test_preserves_chunk_order(self):
        chunks = [
            MemoryChunk(text="AAA", reason=REASON_STABLE_USER_PREFERENCE),
            MemoryChunk(text="BBB", reason=REASON_ACTIVE_PROJECT_MATCH),
        ]
        result = annotate_chunks(chunks)
        assert result.index("AAA") < result.index("BBB")
