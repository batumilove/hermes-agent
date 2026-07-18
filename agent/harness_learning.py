"""Harness-learning primitives for turning failures into runtime structure.

This module is deliberately pure-data and side-effect free. It gives the agent
loop, evaluators, Kanban workers, and future benchmark runners one vocabulary
for failed trajectories without forcing a new model tool into the core schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class FailureType(str, Enum):
    ACTION_REALIZATION = "action_realization"
    ENVIRONMENT_CONTRACT = "environment_contract"
    PROCEDURAL_SKILL_GAP = "procedural_skill_gap"
    TRAJECTORY_DEGRADATION = "trajectory_degradation"
    REASONING = "reasoning"
    EXTERNAL_BLOCKER = "external_blocker"


class HarnessPatchKind(str, Enum):
    PROMPT = "prompt"
    SKILL = "skill"
    TOOL_SCHEMA = "tool_schema"
    ACTION_REALIZATION = "action_realization"
    REGULATOR = "regulator"
    DOCS = "docs"
    TEST = "test"


@dataclass(frozen=True)
class RegressionTask:
    """Minimal fixture shape for a harness regression case."""

    id: str
    prompt: str
    expected_behavior: list[str] = field(default_factory=list)
    forbidden_behavior: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureDiagnosis:
    """Structured failure diagnosis emitted after blocked/failed trajectories."""

    failure_type: FailureType
    confidence: str
    evidence: list[str]
    root_cause_hypothesis: str
    suggested_harness_patch: dict[str, Any]
    regression_task: RegressionTask
    regression_risk: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failure_type"] = self.failure_type.value
        data["regression_task"] = self.regression_task.to_dict()
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def diagnose_repeated_tool_failure(
    *,
    tool_name: str,
    args_hash: str,
    error_preview: str,
    count: int,
) -> FailureDiagnosis:
    """Build a patchable diagnosis for repeated same-tool failure loops."""

    tool = str(tool_name or "tool")
    return FailureDiagnosis(
        failure_type=FailureType.TRAJECTORY_DEGRADATION,
        confidence="high" if count >= 3 else "medium",
        evidence=[
            f"{tool} failed {count} time(s) for args hash {args_hash}: {error_preview}",
        ],
        root_cause_hypothesis=(
            "The trajectory repeated a failing tool path instead of changing "
            "strategy or reporting the blocker."
        ),
        suggested_harness_patch={
            "kind": HarnessPatchKind.REGULATOR.value,
            "description": (
                "Interrupt repeated failed tool calls and require diagnosis, "
                "argument change, alternate tool, or explicit blocker report."
            ),
        },
        regression_task=RegressionTask(
            id=f"repeated-{_slug(tool)}-failure-stop",
            prompt=f"A task causes the same {tool} call to fail repeatedly.",
            expected_behavior=[
                "inspect the latest error",
                "change strategy or arguments",
                "report an external/environment blocker if no safe path remains",
            ],
            forbidden_behavior=[f"repeat the same failing {tool} call unchanged"],
            required_evidence=["tool error preview", "changed strategy or blocker diagnosis"],
        ),
        regression_risk="low",
    )


@dataclass(frozen=True)
class EvidenceDecision:
    requires_evidence: bool
    missing_evidence_for: list[str] = field(default_factory=list)
    message: str = ""


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_ACTION_CLAUSE_SPLIT_RE = re.compile(
    r"(?:,\s*)?\b(?:and(?:\s+then)?|but|however|yet)\b\s*"
)
_HISTORICAL_CLAIM_RE = re.compile(
    r"(?:^\s*(?:earlier|previously|historically|last\s+(?:week|month|year|night))\b|"
    r"\b(?:previously|historically|yesterday|earlier today|last\s+(?:week|month|year|night))\b|"
    r"\bbefore this turn\b|\bin (?:a )?prior turn\b|\bearlier\s*[.!?]?$)"
)
_STATUS_DESCRIPTION_RE = re.compile(
    r"\b(?:current (?:code(?: path)?|state|schema|configuration)|"
    r"inspection (?:shows|found)|readback (?:shows|found))\b"
)
_REPORTING_CLAIM_RE = re.compile(
    r"\b(?:inspect(?:ed|ion)|verif(?:y|ied|ication)|confirm(?:ed|ation)|"
    r"readback|observed|found|logs?\s+(?:show|shows|showed))\b"
)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|we|i've|we've|i have|we have)\b")


def _has_unnegated_verb(clause: str, verb: str) -> bool:
    """Return True when at least one occurrence of *verb* is affirmative."""

    escaped = re.escape(verb)
    previous_end = 0
    for match in re.finditer(rf"\b{escaped}\b", clause):
        prefix = clause[previous_end:match.start()]
        suffix = clause[match.end():]
        negated_before = any(
            re.search(pattern, prefix)
            for pattern in (
                r"\b(?:not(?!\s+only\b)|never|nothing|neither|nor|didn't|wasn't|weren't|"
                r"hasn't|haven't|isn't|aren't|don't|doesn't|can't|couldn't|"
                r"won't|wouldn't|shouldn't)\b[^.!?;,]{0,60}$",
                r"\bno\b[^,;.!?]{0,40}\b(?:was|were|is|are|has been|have been)\s*$",
            )
        )
        negated_after = bool(re.match(r"\s+no\b", suffix))
        if not negated_before and not negated_after:
            return True
        previous_end = match.end()
    return False


def _has_affirmative_side_effect_claim(
    text: str,
    verbs: tuple[str, ...],
    *,
    required_terms: tuple[str, ...] = (),
    context_terms: tuple[str, ...] = (),
) -> bool:
    """Return True only for a local affirmative completion claim.

    The regulator is intentionally conservative: status/history descriptions
    and explicit denials are evidence reports, not claims that this turn
    performed a side effect.  First-person clauses and terse completion forms
    (``Deployed successfully.``) remain covered.
    """

    normalized = str(text or "").lower()
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT_RE.split(normalized)
        if sentence.strip()
    ]

    for sentence_index, sentence in enumerate(sentences):
        # Category nouns may naturally precede a pronoun-only completion claim
        # in the immediately following sentence, but later sentences must not
        # retroactively classify an unrelated earlier action.
        previous = sentences[sentence_index - 1] if sentence_index else ""
        context_scope = f"{previous} {sentence}".strip()
        if required_terms and not all(
            term in context_scope for term in required_terms
        ):
            continue
        if context_terms and not any(
            re.search(rf"\b{re.escape(term)}\b", context_scope)
            for term in context_terms
        ):
            continue

        for raw_clause in _ACTION_CLAUSE_SPLIT_RE.split(sentence):
            clause = raw_clause.strip()
            if not clause:
                continue
            affirmative_verbs = [
                verb for verb in verbs if _has_unnegated_verb(clause, verb)
            ]
            if not affirmative_verbs:
                continue
            first_person = _FIRST_PERSON_RE.search(clause)
            if _HISTORICAL_CLAIM_RE.search(clause):
                continue
            if _STATUS_DESCRIPTION_RE.search(clause) and not first_person:
                continue
            starts_with_verb = any(
                re.match(rf"^(?:successfully\s+)?{re.escape(verb)}\b", clause)
                for verb in affirmative_verbs
            )
            present_state = any(
                re.search(
                    rf"\b(?:is|are)\s+(?:currently\s+)?{re.escape(verb)}\b",
                    clause,
                )
                or (
                    not first_person
                    and re.search(
                        rf"\b(?:is|are)\s+(?:currently\s+)?[^.!?;,]{{0,60}}"
                        rf"\b(?:and|or)\s+(?:currently\s+)?{re.escape(verb)}\b",
                        sentence,
                    )
                )
                for verb in affirmative_verbs
            )
            if present_state:
                continue
            passive_completion = any(
                re.search(
                    rf"\b(?:was|were|has been|have been)\s+{re.escape(verb)}\b",
                    clause,
                )
                for verb in affirmative_verbs
            )
            if passive_completion and _REPORTING_CLAIM_RE.search(clause):
                continue
            if (
                first_person
                or starts_with_verb
                or passive_completion
                or "successfully" in clause
                or "completed" in clause
            ):
                return True
    return False


class SideEffectEvidenceRegulator:
    """Track side-effect tool evidence before final success claims.

    This is a lightweight v0 helper, not a language verifier. It covers common
    high-risk claims where a model says something was sent/created without any
    tool handle in the trajectory.
    """

    def __init__(self) -> None:
        self._evidence: set[str] = set()

    def observe_tool_result(self, tool_name: str, args: dict[str, Any] | None, result: Any) -> None:
        tool = str(tool_name or "")
        text = _compact_text(result)
        if tool in {"write_file", "patch"}:
            if "bytes_written" in text or '"success":true' in text.replace(" ", "").lower():
                self._evidence.add("file")
        if tool == "send_message" and not _looks_like_error(text):
            self._evidence.add("send_message")
        if tool == "cronjob" and not _looks_like_error(text):
            self._evidence.add("cronjob")
        if tool.startswith("github") and not _looks_like_error(text):
            self._evidence.add("github")
        if tool in {"browser_click", "browser_type", "browser_press"} and not _looks_like_error(text):
            self._evidence.add("browser")
        if tool in {"image_generate", "text_to_speech"} and not _looks_like_error(text):
            self._evidence.add("media")
        if tool in {"terminal", "process", "mcp_executor_execute"} and _looks_like_positive_handle(text):
            self._evidence.add("generic_side_effect")

    def evaluate_final_response(self, text: str) -> EvidenceDecision:
        lower = str(text or "").lower()
        missing: list[str] = []
        if _has_affirmative_side_effect_claim(
            lower, ("sent",), required_terms=("message",)
        ) and "send_message" not in self._evidence:
            missing.append("send_message")
        if _has_affirmative_side_effect_claim(
            lower,
            ("created", "opened", "submitted"),
            context_terms=("github", "issue", "pull request", "pr"),
        ) and "github" not in self._evidence:
            missing.append("github")
        if _has_affirmative_side_effect_claim(
            lower,
            ("created", "paused", "resumed", "ran", "scheduled"),
            context_terms=("cron", "cronjob", "scheduler"),
        ) and "cronjob" not in self._evidence:
            missing.append("cronjob")
        if _has_affirmative_side_effect_claim(
            lower, ("uploaded",)
        ) and "media" not in self._evidence and "browser" not in self._evidence and "generic_side_effect" not in self._evidence:
            missing.append("upload")
        if _has_affirmative_side_effect_claim(
            lower, ("deployed",)
        ) and "generic_side_effect" not in self._evidence:
            missing.append("deploy")
        if _has_affirmative_side_effect_claim(
            lower, ("deleted", "removed")
        ) and not ({"cronjob", "github", "browser", "generic_side_effect"} & self._evidence):
            missing.append("delete")
        if missing:
            return EvidenceDecision(
                requires_evidence=True,
                missing_evidence_for=_dedupe(missing),
                message="Side-effect success claim needs a tool result handle/status before finalizing.",
            )
        return EvidenceDecision(requires_evidence=False)


def build_side_effect_evidence_footer(messages: list[dict[str, Any]], final_response: str) -> str:
    """Return a warning footer when final response claims side effects without evidence.

    Only tool results from the current turn are considered. The current turn is
    the suffix after the latest user message, matching finalizer reasoning
    extraction and avoiding stale evidence from prior turns.
    """

    regulator = SideEffectEvidenceRegulator()
    for msg in _current_turn_tool_messages(messages):
        regulator.observe_tool_result(
            str(msg.get("name") or ""),
            {},
            msg.get("content"),
        )
    decision = regulator.evaluate_final_response(final_response)
    if not decision.requires_evidence:
        return ""
    missing = ", ".join(decision.missing_evidence_for)
    return (
        "⚠️ Side-effect evidence regulator: this response claims external or "
        f"state-changing action(s) without current-turn evidence for: {missing}. "
        "Require a tool result handle/status/readback before treating the claim as complete."
    )


def _current_turn_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = 0
    for idx, msg in enumerate(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            start = idx + 1
    return [
        msg for msg in (messages or [])[start:]
        if isinstance(msg, dict) and msg.get("role") == "tool"
    ]


def _compact_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _looks_like_error(text: str) -> bool:
    lower = str(text or "").lower()
    return '"error"' in lower or "error:" in lower or "failed" in lower


def _looks_like_positive_handle(text: str) -> bool:
    lower = str(text or "").lower()
    if _looks_like_error(lower):
        return False
    return any(
        marker in lower
        for marker in (
            "http://",
            "https://",
            "status",
            "exit_code",
        )
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _slug(value: str) -> str:
    return "-".join(part for part in value.lower().replace("_", "-").split("-") if part)
