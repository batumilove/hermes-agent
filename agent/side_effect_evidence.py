"""Current-turn evidence checks for external side-effect claims.

The regulator is observational: it appends a delivery-only qualification when
an assistant claims a completed external/state-changing action without a
matching successful tool result in the current turn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_ACTION_CLAUSE_SPLIT_RE = re.compile(
    r"(?:,\s*)?\b(?:and(?:\s+then)?|but|however|yet|though|although)\b\s*"
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
_STATUS_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+(?:\d+[.)]\s*)?.*"
    r"\b(?:status|state|parity|readback|verification)\b"
    r"\s*(?:[-—:]+\s*)?(?:pass|fail|block(?:ed)?)\s*$"
)
_REPORTING_CLAIM_RE = re.compile(
    r"\b(?:inspect(?:ed|ion)|verif(?:y|ied|ication)|confirm(?:ed|ation)|"
    r"readback|observed|found|logs?\s+(?:show|shows|showed))\b"
)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|we|i've|we've|i have|we have)\b")
_ERROR_MARKERS = (
    "traceback (most recent call last)",
    "unrecognized arguments",
    "invalid choice:",
    "command not found",
    "no such file or directory",
    '"status":"error"',
    '"status": "error"',
)


@dataclass(frozen=True)
class EvidenceDecision:
    requires_evidence: bool
    missing_evidence_for: list[str] = field(default_factory=list)


def _has_unnegated_verb(clause: str, verb: str) -> bool:
    previous_end = 0
    for match in re.finditer(rf"\b{re.escape(verb)}\b", clause):
        prefix = clause[previous_end : match.start()]
        suffix = clause[match.end() :]
        negated_before = any(
            re.search(pattern, prefix)
            for pattern in (
                r"\b(?:not(?!\s+only\b)|never|nothing|neither|nor|didn't|wasn't|weren't|"
                r"hasn't|haven't|isn't|aren't|don't|doesn't|can't|couldn't|"
                r"won't|wouldn't|shouldn't)\b[^.!?;,]{0,60}$",
                r"\bno\b[^,;.!?]{0,40}\b(?:was|were|is|are|has been|have been)\s*$",
            )
        )
        if not negated_before and not re.match(r"\s+no\b", suffix):
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
    normalized = str(text or "").lower()
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(normalized) if part.strip()]

    for sentence_index, sentence in enumerate(sentences):
        previous = sentences[sentence_index - 1] if sentence_index else ""
        context_scope = f"{previous} {sentence}".strip()
        if required_terms and not all(term in context_scope for term in required_terms):
            continue
        if context_terms and not any(
            re.search(rf"\b{re.escape(term)}\b", context_scope) for term in context_terms
        ):
            continue

        for raw_clause in _ACTION_CLAUSE_SPLIT_RE.split(sentence):
            clause = raw_clause.strip()
            if not clause:
                continue
            heading_scope = clause
            if re.fullmatch(r"\s*#{1,6}\s+\d+[.)]\s*", previous):
                heading_scope = f"{previous} {clause}"
            if _STATUS_HEADING_RE.search(heading_scope) and not _FIRST_PERSON_RE.search(
                heading_scope
            ):
                continue

            affirmative_verbs = [verb for verb in verbs if _has_unnegated_verb(clause, verb)]
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
                re.search(rf"\b(?:is|are)\s+(?:currently\s+)?{re.escape(verb)}\b", clause)
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


def _current_turn_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = 0
    for index, message in enumerate(messages or []):
        if isinstance(message, dict) and message.get("role") == "user":
            start = index + 1
    return [
        message
        for message in (messages or [])[start:]
        if isinstance(message, dict) and message.get("role") == "tool"
    ]


def _parsed_result(content: Any) -> dict[str, Any] | None:
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _tool_result_succeeded(tool_name: str, content: Any) -> bool:
    result = _parsed_result(content)
    if result is None:
        return False
    if result.get("error") not in (None, "", False):
        return False
    if result.get("success") is False or result.get("ok") is False:
        return False
    status = str(result.get("status") or "").lower()
    if status in {"error", "failed", "failure", "blocked"}:
        return False

    tool = str(tool_name or "").lower()
    if "exit_code" in result:
        try:
            if int(str(result["exit_code"])) != 0:
                return False
        except (TypeError, ValueError):
            return False

    if tool == "terminal":
        try:
            succeeded = int(str(result.get("exit_code"))) == 0
        except (TypeError, ValueError):
            return False
    elif tool == "execute_code":
        succeeded = status == "success"
        if "exit_code" in result:
            try:
                succeeded = succeeded and int(str(result["exit_code"])) == 0
            except (TypeError, ValueError):
                return False
    else:
        positive_status = status in {
            "success",
            "succeeded",
            "ready",
            "running",
            "completed",
            "created",
            "scheduled",
        }
        positive_flag = result.get("success") is True or result.get("ok") is True
        positive_handle = any(
            result.get(key) not in (None, "", False, [], {})
            for key in (
                "id",
                "message_id",
                "job_id",
                "session_id",
                "execution_id",
                "url",
                "image",
                "path",
                "ref",
            )
        )
        succeeded = positive_status or positive_flag or positive_handle

    compact = json.dumps(
        result, ensure_ascii=False, sort_keys=True, default=str
    ).lower()
    return succeeded and not any(marker in compact for marker in _ERROR_MARKERS)


def _evidence_from_messages(messages: list[dict[str, Any]]) -> set[str]:
    evidence: set[str] = set()
    for message in _current_turn_tool_messages(messages):
        tool = str(message.get("name") or "")
        if not _tool_result_succeeded(tool, message.get("content")):
            continue
        if tool == "send_message":
            evidence.add("send_message")
        if tool == "cronjob":
            evidence.add("cronjob")
        if tool.startswith("github"):
            evidence.add("github")
        if tool in {"browser_click", "browser_type", "browser_press"}:
            evidence.add("browser")
        if tool in {"image_generate", "text_to_speech"}:
            evidence.add("media")
        if tool in {
            "terminal",
            "execute_code",
            "process",
            "mcp_executor_execute",
            "mcp__executor__execute",
        }:
            evidence.add("generic_side_effect")
    return evidence


def evaluate_side_effect_evidence(
    messages: list[dict[str, Any]], final_response: str
) -> EvidenceDecision:
    evidence = _evidence_from_messages(messages)
    missing: list[str] = []

    if _has_affirmative_side_effect_claim(
        final_response, ("sent",), required_terms=("message",)
    ) and "send_message" not in evidence:
        missing.append("send_message")
    if _has_affirmative_side_effect_claim(
        final_response,
        ("created", "opened", "submitted"),
        context_terms=("github", "issue", "pull request", "pr"),
    ) and "github" not in evidence:
        missing.append("github")
    if _has_affirmative_side_effect_claim(
        final_response,
        ("created", "paused", "resumed", "ran", "scheduled"),
        context_terms=("cron", "cronjob", "scheduler"),
    ) and "cronjob" not in evidence:
        missing.append("cronjob")
    if _has_affirmative_side_effect_claim(final_response, ("uploaded",)) and not (
        {"media", "browser", "generic_side_effect"} & evidence
    ):
        missing.append("upload")
    if _has_affirmative_side_effect_claim(final_response, ("deployed",)) and (
        "generic_side_effect" not in evidence
    ):
        missing.append("deploy")
    if _has_affirmative_side_effect_claim(
        final_response,
        ("deleted", "removed"),
        context_terms=(
            "remote",
            "repository",
            "repo",
            "branch",
            "issue",
            "pull request",
            "pr",
            "cronjob",
            "cron",
            "scheduler",
            "container",
            "image",
            "bucket",
            "artifact",
            "deployment",
            "service",
            "server",
            "domain",
            "record",
            "account",
            "user",
            "message",
            "job",
            "workflow",
        ),
    ) and not (
        {"cronjob", "github", "browser", "generic_side_effect"} & evidence
    ):
        missing.append("delete")

    return EvidenceDecision(bool(missing), list(dict.fromkeys(missing)))


def build_side_effect_evidence_footer(
    messages: list[dict[str, Any]], final_response: str
) -> str:
    """Return a delivery-only warning for unsupported current-turn claims."""

    decision = evaluate_side_effect_evidence(messages, final_response)
    if not decision.requires_evidence:
        return ""
    missing = ", ".join(decision.missing_evidence_for)
    return (
        "⚠️ Side-effect evidence regulator: this response claims external or "
        f"state-changing action(s) without current-turn evidence for: {missing}. "
        "Require a tool result handle/status/readback before treating the claim as complete."
    )
