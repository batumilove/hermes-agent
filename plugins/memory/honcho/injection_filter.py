"""Injection-time memory filtering and reason annotation for Honcho.

This module provides:
- Reason labels for injected memory chunks (why was this selected?)
- Content-based classification of memory text into reason categories
- Stale/task-progress/contradicted memory suppression at injection time
- Annotation formatting that preserves raw text while adding reason metadata

Design principles:
- Pure injection-time behavior — never modifies Honcho's stored memories
- Additive metadata — augment rather than replace
- Graceful fallback — unknown content gets REASON_UNKNOWN, not an error
- Deterministic — same input always produces same output
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Reason label constants
# ---------------------------------------------------------------------------

REASON_EXACT_ENTITY_MATCH = "exact_entity_match"
REASON_SEMANTIC_MATCH = "semantic_match"
REASON_RECENT_CORRECTION = "recent_correction"
REASON_STABLE_USER_PREFERENCE = "stable_user_preference"
REASON_ACTIVE_PROJECT_MATCH = "active_project_match"
REASON_SESSION_CONTINUITY = "session_continuity"
REASON_TASK_PROGRESS = "task_progress"
REASON_CONTRADICTED = "contradicted"
REASON_STALE = "stale"
REASON_UNKNOWN = "unknown"

# Reasons that are suppressed by default during injection.
_SUPPRESSED_REASONS = frozenset({
    REASON_TASK_PROGRESS,
    REASON_STALE,
    REASON_CONTRADICTED,
})

# ---------------------------------------------------------------------------
# MemoryChunk dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryChunk:
    """A single memory item with classification metadata.

    Attributes:
        text: The raw memory content string.
        reason: Why this chunk was selected (one of the REASON_* constants).
        source: Origin of the chunk (e.g. "base_context", "dialectic", "tool").
        is_stale: Explicit staleness flag — when True, the chunk is suppressed
                  regardless of its reason.
        metadata: Optional additional key-value pairs for diagnostics.
    """

    text: str
    reason: str = REASON_UNKNOWN
    source: str = ""
    is_stale: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Content-based reason classification
# ---------------------------------------------------------------------------

# Pattern precedence (first match wins):
# 1. stable_user_preference — "user prefers/likes/wants/always/never"
# 2. recent_correction — "corrected/updated/fixed: was X, now Y"
# 3. task_progress — mentions PRs, commits, issues, phases
# 4. active_project_match — mentions "project/uses/built on/stack"
# 5. session_continuity — "previous session/we discussed/earlier"
# 6. unknown — fallback

_USER_PREF_RE = re.compile(
    r"(user|he|she|they)\s+(prefers?|likes?|wants?|always|never|love[sd]?|hat(?:e|es)|avoid[sd]?|insist[sd]?|use[sd]?)\b",
    re.IGNORECASE,
)

_CORRECTION_RE = re.compile(
    r"\b(corrected|correction|updated|fix:|fixed:|was .+ now .+|changed from .+ to)\b",
    re.IGNORECASE,
)

_TASK_PROGRESS_RE = re.compile(
    r"\b(submitted|merged|closed|opened)\s+(pr|pull request|issue|mr)\b"
    r"|pr\s*#?\d+"
    r"|commit\s+[a-f0-9]{4,}"
    r"|phase\s+\d+\s+(done|complete|finished)"
    r"|\bdone\b.*\bpr\b"
    r"|\bcompleted?\b.*\b(issue|pr|task)\b",
    re.IGNORECASE,
)

_PROJECT_RE = re.compile(
    r"\b(project|repo|repository|codebase|stack)\s+(use[sd]?|build[sd]?|based|built|run[sd]?|deploy[sd]?|test[sd]?)\b"
    r"|\b(uses|built on|builds on|runs on|deployed on)\b\s+\w+",
    re.IGNORECASE,
)

_SESSION_CONTINUITY_RE = re.compile(
    r"\b(previous session|earlier session|last session|last time|we discussed|we talked about)\b",
    re.IGNORECASE,
)


def classify_memory(text: str) -> str:
    """Classify a memory text into a reason label.

    Uses pattern matching on the text content. Returns one of the
    REASON_* constants. Order of checks matters — higher-priority
    patterns are checked first.
    """
    if not text or not text.strip():
        return REASON_UNKNOWN

    # 1. Stable user preferences — highest priority
    if _USER_PREF_RE.search(text):
        return REASON_STABLE_USER_PREFERENCE

    # 2. Recent corrections
    if _CORRECTION_RE.search(text):
        return REASON_RECENT_CORRECTION

    # 3. Task progress (PR/commit/issue mentions)
    if _TASK_PROGRESS_RE.search(text):
        return REASON_TASK_PROGRESS

    # 4. Active project match
    if _PROJECT_RE.search(text):
        return REASON_ACTIVE_PROJECT_MATCH

    # 5. Session continuity
    if _SESSION_CONTINUITY_RE.search(text):
        return REASON_SESSION_CONTINUITY

    # 6. Fallback
    return REASON_UNKNOWN


# ---------------------------------------------------------------------------
# Stale / task-progress suppression
# ---------------------------------------------------------------------------


def filter_stale_memories(chunks: List[MemoryChunk]) -> List[MemoryChunk]:
    """Filter out stale, contradicted, and task-progress memory chunks.

    Suppression rules:
    - Any chunk with is_stale=True is always suppressed.
    - Chunks with reason in _SUPPRESSED_REASONS (task_progress, stale,
      contradicted) are suppressed.
    - All other reasons pass through.

    Does NOT mutate the input list. Returns a new list.
    """
    result = []
    for chunk in chunks:
        if chunk.is_stale:
            continue
        if chunk.reason in _SUPPRESSED_REASONS:
            continue
        result.append(chunk)
    return result


# ---------------------------------------------------------------------------
# Reason annotation formatting
# ---------------------------------------------------------------------------


def annotate_chunk(chunk: MemoryChunk) -> str:
    """Format a single MemoryChunk with its reason label as an inline annotation.

    The annotation is a compact prefix like:
        [reason: stable_user_preference] User prefers dark mode

    Source is included when non-empty:
        [reason: stable_user_preference, source: base_context] User prefers dark mode
    """
    label = f"reason: {chunk.reason}"
    if chunk.source:
        label += f", source: {chunk.source}"
    return f"[{label}] {chunk.text}"


def annotate_chunks(chunks: List[MemoryChunk]) -> str:
    """Annotate a list of MemoryChunks and join them with double newlines.

    Returns the formatted string ready for injection into the prompt.
    Returns empty string for empty input.
    """
    if not chunks:
        return ""
    return "\n\n".join(annotate_chunk(c) for c in chunks)
