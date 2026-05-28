"""Integration tests for prefetch() reason labels and stale suppression.

Tests that the HonchoMemoryProvider.prefetch() method correctly:
1. Annotates base context and dialectic results with reason labels
2. Suppresses task-progress/stale/contradicted content at injection time
3. Preserves stable user preferences and active-project context through filtering
4. Produces annotated output that includes the raw text plus reason metadata
"""

import json
from unittest.mock import MagicMock, patch

from plugins.memory.honcho import HonchoMemoryProvider
from plugins.memory.honcho.injection_filter import (
    REASON_STABLE_USER_PREFERENCE,
    REASON_ACTIVE_PROJECT_MATCH,
    REASON_TASK_PROGRESS,
    REASON_RECENT_CORRECTION,
    REASON_UNKNOWN,
)


def _settle_prewarm(provider):
    """Wait for prewarm thread and reset state."""
    if provider._prefetch_thread:
        provider._prefetch_thread.join(timeout=3.0)
    with provider._prefetch_lock:
        provider._prefetch_result = ""
        provider._prefetch_result_fired_at = -999
    provider._prefetch_thread = None
    provider._prefetch_thread_started_at = 0.0
    provider._last_dialectic_turn = -999
    provider._dialectic_empty_streak = 0


def _make_provider(cfg_extra=None):
    """Create a HonchoMemoryProvider with mocked dependencies for prefetch tests."""
    from plugins.memory.honcho.client import HonchoClientConfig

    defaults = dict(api_key="test-key", enabled=True, recall_mode="hybrid")
    if cfg_extra:
        defaults.update(cfg_extra)
    cfg = HonchoClientConfig(**defaults)

    provider = HonchoMemoryProvider()
    mock_manager = MagicMock()
    mock_session = MagicMock()
    mock_session.messages = []
    mock_manager.get_or_create.return_value = mock_session
    # pop_context_result returns None by default so it doesn't override cache
    mock_manager.pop_context_result.return_value = None

    with patch("plugins.memory.honcho.client.HonchoClientConfig.from_global_config", return_value=cfg), \
         patch("plugins.memory.honcho.client.get_honcho_client", return_value=MagicMock()), \
         patch("plugins.memory.honcho.session.HonchoSessionManager", return_value=mock_manager), \
         patch("hermes_constants.get_hermes_home", return_value=MagicMock()):
        provider.initialize(session_id="test-session-injection")

    _settle_prewarm(provider)
    return provider


# ---------------------------------------------------------------------------
# Reason label injection
# ---------------------------------------------------------------------------


class TestPrefetchReasonLabels:
    """prefetch() annotates output with reason labels."""

    def test_base_context_gets_reason_label(self):
        """Base context containing user preferences is annotated with a reason."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "User prefers concise responses"
        provider._turn_count = 5
        provider._last_dialectic_turn = 0

        # No dialectic pending
        result = provider.prefetch("what is my preferred style?")

        assert result != ""
        assert "User prefers concise responses" in result
        assert REASON_STABLE_USER_PREFERENCE in result

    def test_dialectic_result_gets_reason_label(self):
        """Dialectic supplement is annotated with a reason label."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = ""
        provider._turn_count = 5

        # Simulate a pending dialectic result
        provider._prefetch_result = "Project uses pytest with xdist"
        provider._prefetch_result_fired_at = 3
        provider._last_dialectic_turn = 3

        result = provider.prefetch("how does the project test?")

        assert result != ""
        assert "Project uses pytest with xdist" in result
        assert REASON_ACTIVE_PROJECT_MATCH in result

    def test_both_layers_labeled(self):
        """Both base context and dialectic result get separate reason labels."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "User prefers dark mode"
        provider._turn_count = 5

        provider._prefetch_result = "Project uses React and TypeScript"
        provider._prefetch_result_fired_at = 3
        provider._last_dialectic_turn = 3

        result = provider.prefetch("tell me about the stack")

        assert result != ""
        assert "User prefers dark mode" in result
        assert "Project uses React and TypeScript" in result
        assert REASON_STABLE_USER_PREFERENCE in result
        assert REASON_ACTIVE_PROJECT_MATCH in result

    def test_unknown_reason_for_generic_content(self):
        """Content that doesn't match any specific pattern gets REASON_UNKNOWN."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "The weather is sunny"
        provider._turn_count = 5
        provider._last_dialectic_turn = 0

        result = provider.prefetch("what's up?")

        assert result != ""
        assert "The weather is sunny" in result
        assert REASON_UNKNOWN in result


# ---------------------------------------------------------------------------
# Stale/task-progress suppression
# ---------------------------------------------------------------------------


class TestPrefetchStaleSuppression:
    """prefetch() suppresses stale/task-progress/contradicted content."""

    def test_task_progress_in_base_context_suppressed(self):
        """Base context containing task-progress is suppressed from output."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "Fixed bug X and submitted PR #123"
        provider._turn_count = 5
        provider._last_dialectic_turn = 0

        result = provider.prefetch("how are things going?")

        # Task-progress should be suppressed; result is empty
        assert result == ""

    def test_task_progress_in_dialectic_suppressed(self):
        """Dialectic result containing task-progress is suppressed."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = ""
        provider._turn_count = 5

        provider._prefetch_result = "Phase 2 done, commit abc1234"
        provider._prefetch_result_fired_at = 3
        provider._last_dialectic_turn = 3

        result = provider.prefetch("what's the status?")

        assert result == ""

    def test_preference_survives_when_task_progress_filtered(self):
        """User preferences survive even when task-progress content is also present."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "User prefers concise responses"
        provider._turn_count = 5

        provider._prefetch_result = "Phase 2 done, commit abc1234"
        provider._prefetch_result_fired_at = 3
        provider._last_dialectic_turn = 3

        result = provider.prefetch("tell me about preferences")

        assert result != ""
        assert "User prefers concise responses" in result
        assert REASON_STABLE_USER_PREFERENCE in result
        # Task-progress dialectic should NOT appear
        assert "Phase 2 done" not in result

    def test_correction_survives_filtering(self):
        """Recent corrections are preserved through the filter."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "Corrected: user timezone is PST, not EST"
        provider._turn_count = 5
        provider._last_dialectic_turn = 0

        result = provider.prefetch("what timezone?")

        assert result != ""
        assert "Corrected: user timezone is PST" in result
        assert REASON_RECENT_CORRECTION in result

    def test_empty_after_all_suppressed(self):
        """When all parts are suppressed, prefetch returns empty string."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "Phase 2 done, commit abc1234"
        provider._turn_count = 5

        provider._prefetch_result = "Submitted PR #456 and closed issue #789"
        provider._prefetch_result_fired_at = 3
        provider._last_dialectic_turn = 3

        result = provider.prefetch("status update")

        assert result == ""


# ---------------------------------------------------------------------------
# Annotation format
# ---------------------------------------------------------------------------


class TestPrefetchAnnotationFormat:
    """The annotation format is machine-readable and compact."""

    def test_annotation_includes_source_label(self):
        """Annotated output includes source label (base_context or dialectic)."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "User prefers dark mode"
        provider._turn_count = 5
        provider._last_dialectic_turn = 0

        result = provider.prefetch("preferences?")

        assert "source:" in result or "base_context" in result

    def test_annotation_is_parseable(self):
        """The [reason: X, source: Y] prefix is structured and parseable."""
        provider = _make_provider()
        provider._session_key = "test"
        provider._base_context_cache = "User prefers dark mode"
        provider._turn_count = 5
        provider._last_dialectic_turn = 0

        result = provider.prefetch("preferences?")

        # Should contain at least one [reason: ...] annotation
        assert "[reason:" in result
