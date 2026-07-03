"""Harness-learning primitives for turning failures into runtime structure.

This module is deliberately pure-data and side-effect free. It gives the agent
loop, evaluators, Kanban workers, and future benchmark runners one vocabulary
for failed trajectories without forcing a new model tool into the core schema.
"""

from __future__ import annotations

import json
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
        if "sent" in lower and "message" in lower and "send_message" not in self._evidence:
            missing.append("send_message")
        if ("github" in lower or "issue" in lower or "pull request" in lower or " pr " in f" {lower} ") and (
            "created" in lower or "opened" in lower or "submitted" in lower
        ) and "github" not in self._evidence:
            missing.append("github")
        if (
            any(word in lower for word in ("scheduled", "paused", "resumed"))
            and "cron" in lower
            and "cronjob" not in self._evidence
        ):
            missing.append("cronjob")
        if "uploaded" in lower and "media" not in self._evidence and "browser" not in self._evidence and "generic_side_effect" not in self._evidence:
            missing.append("upload")
        if "deployed" in lower and "generic_side_effect" not in self._evidence:
            missing.append("deploy")
        if ("deleted" in lower or "removed" in lower) and not ({"cronjob", "github", "browser", "generic_side_effect"} & self._evidence):
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
