"""Harness-learning primitives for turning failures into runtime structure.

This module is deliberately pure-data and side-effect free. It gives the agent
loop, evaluators, Kanban workers, and future benchmark runners one vocabulary
for failed trajectories without forcing a new model tool into the core schema.
"""

from __future__ import annotations

import json
import re
import shlex
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


@dataclass(frozen=True)
class KanbanClaimDecision:
    """Fail-closed decision for ordinary-controller Kanban state claims."""

    rejected: bool
    task_ids: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    message: str = ""


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
_KANBAN_TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{8,}\b", re.IGNORECASE)
_KANBAN_STATUS_RE = re.compile(
    r"\b(?:ready|running|todo|blocked|scheduled|done|archived|crashed|failed)\b",
    re.IGNORECASE,
)
_KANBAN_FORENSIC_RE = re.compile(
    r"\b(?:previous|prior|earlier|historical)\s+(?:assistant|agent|report)|"
    r"\b(?:assistant|agent)\s+(?:claimed|reported)|\bnever existed\b|"
    r"\bphantom(?:\s+(?:task|card|id))?\b",
    re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_BLOCKQUOTE_LINE_RE = re.compile(r"(?m)^\s*>.*$")
_KANBAN_NONASSERTIVE_RE = re.compile(
    r"\b(?:if|hypothetically|would|could|might)\b|"
    r"\b(?:does not|doesn't|is not|isn't|was not|wasn't|never)\b|"
    r"\b(?:does not mean|is not equivalent to|for example|example:)\b",
    re.IGNORECASE,
)


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
        status_heading_scope = sentence
        if re.fullmatch(r"\s*#{1,6}\s+\d+[.)]\s*", previous):
            # Sentence splitting treats a numbered Markdown heading's ``7.``
            # as punctuation. Rejoin only that narrow heading prefix; plain
            # status-shaped action claims must remain regulated.
            status_heading_scope = f"{previous} {sentence}"
        if _STATUS_HEADING_RE.search(status_heading_scope) and not _FIRST_PERSON_RE.search(
            status_heading_scope
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
        if tool in {"terminal", "execute_code"} and _tool_result_succeeded(tool, result):
            self._evidence.add("generic_side_effect")
        if tool in {"process", "mcp_executor_execute"} and _looks_like_positive_handle(text):
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


def _tool_result_succeeded(tool_name: str, result: Any, args: str = "") -> bool:
    """Require the current tool's documented top-level success contract."""

    text = _compact_text(result)
    try:
        parsed = json.loads(text) if isinstance(text, str) else result
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("error") not in (None, "", False):
        return False

    tool = str(tool_name or "").lower()
    if tool.startswith("kanban_"):
        operation = _kanban_operation_kind(tool, args)
        if operation == "show":
            # Native kanban_show returns the task object directly, without the
            # mutation handlers' {ok:true} envelope.
            task = parsed.get("task")
            succeeded = (
                isinstance(task, dict)
                and isinstance(task.get("id"), str)
                and bool(task["id"].strip())
                and isinstance(task.get("status"), str)
                and bool(task["status"].strip())
            )
        elif operation == "list":
            # Native kanban_list likewise returns a bounded task collection.
            tasks = parsed.get("tasks")
            succeeded = (
                isinstance(tasks, list)
                and type(parsed.get("count")) is int
                and parsed["count"] == len(tasks)
                and all(
                    isinstance(task, dict)
                    and isinstance(task.get("id"), str)
                    and bool(task["id"].strip())
                    and isinstance(task.get("status"), str)
                    and bool(task["status"].strip())
                    for task in tasks
                )
            )
        else:
            succeeded = parsed.get("ok") is True or parsed.get("success") is True
    elif tool == "terminal":
        try:
            succeeded = int(parsed.get("exit_code")) == 0
        except (TypeError, ValueError):
            succeeded = False
    elif tool == "execute_code":
        succeeded = str(parsed.get("status") or "").lower() == "success"
        exit_code = parsed.get("exit_code")
        if exit_code is not None:
            try:
                succeeded = succeeded and int(str(exit_code)) == 0
            except (TypeError, ValueError):
                succeeded = False
    else:
        return False
    if not succeeded:
        return False

    lower = text.lower()
    return not any(
        marker in lower
        for marker in (
            "traceback (most recent call last)",
            "unrecognized arguments",
            "invalid choice:",
            "command not found",
            "no such file or directory",
            "stderr: hermes: error:",
            '"status":"error"',
            '"status": "error"',
        )
    )


def _terminal_is_direct_kanban_command(args: str) -> bool:
    """Allow terminal evidence only from a direct first-party Kanban CLI call."""

    try:
        payload = json.loads(str(args or ""))
    except (json.JSONDecodeError, TypeError):
        return False
    command = payload.get("command") if isinstance(payload, dict) else None
    if not isinstance(command, str) or not command.strip():
        return False
    if "$" in command or "`" in command or "\n" in command:
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    if any(token and set(token) <= set(";&|()<> ") for token in tokens):
        return False
    return (
        len(tokens) >= 3
        and tokens[0] == "hermes"
        and tokens[1].lower() == "kanban"
    )


def _current_turn_tool_records(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Pair current-turn tool results with the arguments that produced them."""

    start = 0
    for idx, msg in enumerate(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            start = idx + 1
    calls: dict[str, tuple[str, str]] = {}
    records: list[dict[str, str]] = []
    for msg in (messages or [])[start:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                calls[str(call.get("id") or "")] = (
                    str(fn.get("name") or ""),
                    _compact_text(fn.get("arguments") or ""),
                )
        elif msg.get("role") == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            if not call_id or call_id not in calls:
                continue
            call_name, call_args = calls[call_id]
            records.append(
                {
                    "name": call_name,
                    "args": call_args,
                    "result": _compact_text(msg.get("content")),
                }
            )
    return records


def _kanban_operation_kind(name: str, args: str) -> str:
    haystack = f"{name} {args}".lower().replace("_", " ")
    for kind in (
        "create", "show", "list", "edit", "complete", "link", "unlink",
        "block", "unblock", "comment", "claim", "promote", "schedule",
        "archive", "assign", "dispatch",
    ):
        if re.search(rf"\bkanban\b[^\n]{{0,120}}\b{kind}\b", haystack) or re.search(
            rf"\bkanban\s+{kind}\b", haystack
        ):
            return kind
    return ""


def _claimed_kanban_operation(fresh_action: re.Match[str] | None) -> set[str]:
    if fresh_action is None:
        return set()
    action = fresh_action.group(0).lower()
    if re.search(r"\b(?:create|created|add|added)\b", action):
        return {"create"}
    if re.search(r"\b(?:queue|queued|start|started)\b", action):
        return {"create", "dispatch"}
    if re.search(r"\b(?:assign|assigned|reassign|reassigned)\b", action):
        return {"assign", "edit"}
    for stem, operation in (
        ("edit", "edit"),
        ("unlink", "unlink"),
        ("link", "link"),
        ("unblock", "unblock"),
        ("block", "block"),
        ("comment", "comment"),
        ("claim", "claim"),
        ("complet", "complete"),
        ("promot", "promote"),
        ("schedul", "schedule"),
        ("archiv", "archive"),
    ):
        if stem in action:
            return {operation}
    return set()


def _claimed_statuses_for_task(text: str, task_id: str) -> set[str]:
    """Bind statuses to an exact task id, including an adjacent pronoun clause."""

    task_id_lower = task_id.lower()
    statuses: set[str] = set()
    sentences = _SENTENCE_SPLIT_RE.split(text.lower())
    for index, sentence in enumerate(sentences):
        if not re.search(
            rf"(?<![0-9a-f]){re.escape(task_id_lower)}(?![0-9a-f])", sentence
        ):
            continue
        sentence_statuses: set[str] = set()
        for clause in _ACTION_CLAUSE_SPLIT_RE.split(sentence):
            if _KANBAN_NONASSERTIVE_RE.search(clause):
                continue
            sentence_statuses.update(
                status.group(0).lower() for status in _KANBAN_STATUS_RE.finditer(clause)
            )
        statuses.update(sentence_statuses)
        if index + 1 >= len(sentences):
            continue
        following = sentences[index + 1].strip()
        if _KANBAN_TASK_ID_RE.search(following) or not re.match(
            r"^(?:they|both|all|these\s+tasks?|the\s+tasks?|it|this\s+task)\b",
            following,
        ):
            continue
        for clause in _ACTION_CLAUSE_SPLIT_RE.split(following):
            if _KANBAN_NONASSERTIVE_RE.search(clause):
                continue
            statuses.update(
                status.group(0).lower() for status in _KANBAN_STATUS_RE.finditer(clause)
            )
    return statuses


def _iter_nested_values(value: Any):
    """Yield decoded JSON containers, including JSON embedded in wrapper strings."""

    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_nested_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nested_values(child)
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate and candidate[0] in "[{\"":
            try:
                decoded = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                return
            if decoded != value:
                yield from _iter_nested_values(decoded)


def _readback_root(result: str, *, terminal_envelope: bool = False) -> Any:
    """Decode a native result or terminal JSON output without recursive descent."""

    try:
        root: Any = json.loads(str(result or ""))
    except (json.JSONDecodeError, TypeError):
        return None
    # Only terminal wraps the CLI's JSON object in its top-level output string.
    # Native show/list results must never let an unrelated ``output`` field
    # override their documented top-level current-state rows.
    if (
        terminal_envelope
        and isinstance(root, dict)
        and isinstance(root.get("output"), str)
    ):
        try:
            root = json.loads(root["output"])
        except (json.JSONDecodeError, TypeError):
            return None
    return root


def _result_task_evidence(
    result: str, operation: str = "", *, terminal_envelope: bool = False
) -> tuple[set[str], dict[str, set[str]]]:
    """Extract task evidence, scoping readbacks to current-state rows only."""

    if operation in {"show", "list"}:
        root = _readback_root(result, terminal_envelope=terminal_envelope)
        rows: list[Any]
        if operation == "show":
            if isinstance(root, dict) and isinstance(root.get("task"), dict):
                rows = [root["task"]]
            else:
                rows = [root]
        else:
            if isinstance(root, list):
                rows = root
            elif isinstance(root, dict):
                rows = root.get("tasks", [])
            else:
                rows = []
        task_ids: set[str] = set()
        statuses: dict[str, set[str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            exact_id = str(row.get("id") or row.get("task_id") or "").lower()
            status = row.get("status")
            if not _KANBAN_TASK_ID_RE.fullmatch(exact_id):
                continue
            task_ids.add(exact_id)
            if isinstance(status, str) and _KANBAN_STATUS_RE.fullmatch(status.strip()):
                statuses.setdefault(exact_id, set()).add(status.strip().lower())
        return task_ids, statuses

    try:
        root = json.loads(str(result or ""))
    except (json.JSONDecodeError, TypeError):
        root = str(result or "")

    task_ids: set[str] = set()
    statuses: dict[str, set[str]] = {}
    for value in _iter_nested_values(root):
        if not isinstance(value, dict):
            continue
        ids = {
            str(value[key]).lower()
            for key in ("id", "task_id")
            if isinstance(value.get(key), str)
            and _KANBAN_TASK_ID_RE.fullmatch(str(value[key]))
        }
        task_ids.update(ids)
        status = value.get("status")
        if isinstance(status, str) and _KANBAN_STATUS_RE.fullmatch(status.strip()):
            for exact_id in ids:
                statuses.setdefault(exact_id, set()).add(status.strip().lower())

    for value in _iter_nested_values(root):
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if candidate and candidate[0] in "[{":
            try:
                if isinstance(json.loads(candidate), (dict, list)):
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        for line in value.replace("\\\"", '"').splitlines():
            line_ids = {
                match.group(0).lower() for match in _KANBAN_TASK_ID_RE.finditer(line)
            }
            task_ids.update(line_ids)
            if len(line_ids) != 1:
                continue
            line_statuses = {
                match.group(0).lower() for match in _KANBAN_STATUS_RE.finditer(line)
            }
            exact_id = next(iter(line_ids))
            statuses.setdefault(exact_id, set()).update(line_statuses)
    return task_ids, statuses


def _result_has_task_id(
    result: str,
    task_id: str,
    operation: str = "",
    *,
    terminal_envelope: bool = False,
) -> bool:
    task_ids, _ = _result_task_evidence(
        result, operation, terminal_envelope=terminal_envelope
    )
    return task_id.lower() in task_ids


def _result_has_status(
    result: str,
    task_id: str,
    status: str,
    operation: str = "",
    *,
    terminal_envelope: bool = False,
) -> bool:
    _, statuses = _result_task_evidence(
        result, operation, terminal_envelope=terminal_envelope
    )
    return status.lower() in statuses.get(task_id.lower(), set())


def evaluate_controller_kanban_claims(
    messages: list[dict[str, Any]], final_response: str
) -> KanbanClaimDecision:
    """Reject fresh controller Kanban claims without mutation and readback proof.

    This guard intentionally operates only on explicit ``t_<hex>`` references.
    Historical/forensic discussion is excluded.  Creation/mutation claims need
    a successful current-turn mutation result *and* a separate show/list
    readback; status-only claims need the readback.
    """

    text = str(final_response or "")
    scan_text = _BLOCKQUOTE_LINE_RE.sub("", _FENCED_CODE_RE.sub("", text))
    fresh_action = re.search(
        r"(?:^|[.!?]\s+|\n\s*(?:[-*]\s*)?)"
        r"(?:(?:i|we)\s+(?:have\s+)?)?"
        r"(?:created?|added?|queued?|started?|assigned?|reassigned?|edited?|linked?|"
        r"unblocked?|blocked?|commented?|claimed?|completed?|promoted?|scheduled?|archived?)\b",
        scan_text,
        re.IGNORECASE,
    )
    relevant_segments = []
    for segment in _SENTENCE_SPLIT_RE.split(scan_text):
        if not _KANBAN_TASK_ID_RE.search(segment):
            continue
        assertive_id_clause = any(
            _KANBAN_TASK_ID_RE.search(clause)
            and not _KANBAN_NONASSERTIVE_RE.search(clause)
            for clause in _ACTION_CLAUSE_SPLIT_RE.split(segment)
        )
        if not assertive_id_clause:
            continue
        if _KANBAN_FORENSIC_RE.search(segment) and not re.search(
            r"\b(?:i|we)\s+(?:have\s+)?(?:created?|added?|queued?|started?|"
            r"assigned?|reassigned?|edited?|linked?|unblocked?|blocked?|"
            r"commented?|claimed?|completed?|promoted?|scheduled?|archived?)\b",
            segment,
            re.IGNORECASE,
        ):
            continue
        relevant_segments.append(segment)
    task_ids = _dedupe(
        match.group(0).lower()
        for segment in relevant_segments
        for match in _KANBAN_TASK_ID_RE.finditer(segment)
    )
    if not task_ids:
        return KanbanClaimDecision(rejected=False)

    has_action = bool(fresh_action)
    has_status = any(_claimed_statuses_for_task(scan_text, task_id) for task_id in task_ids)
    if not has_action and not has_status:
        return KanbanClaimDecision(rejected=False)

    expected_operations = _claimed_kanban_operation(fresh_action)
    records = _current_turn_tool_records(messages)
    missing: list[str] = []
    for task_id in task_ids:
        matching = [
            record for record in records
            if record["name"].lower() != "execute_code"
            and (
                record["name"].lower() != "terminal"
                or _terminal_is_direct_kanban_command(record["args"])
            )
            and _result_has_task_id(
                record["result"],
                task_id,
                _kanban_operation_kind(record["name"], record["args"]),
                terminal_envelope=record["name"].lower() == "terminal",
            )
            and _tool_result_succeeded(
                record["name"], record["result"], record["args"]
            )
        ]
        if has_action and not any(
            _kanban_operation_kind(record["name"], record["args"])
            in expected_operations
            for record in matching
        ):
            missing.append(f"{task_id}:mutation")
        claimed_statuses = _claimed_statuses_for_task(scan_text, task_id)
        if has_action or claimed_statuses:
            readbacks = [
                record
                for record in matching
                if _kanban_operation_kind(record["name"], record["args"])
                in {"show", "list"}
            ]
            status_matches = bool(readbacks) and (
                not claimed_statuses
                or all(
                    any(
                        _result_has_status(
                            record["result"],
                            task_id,
                            status,
                            _kanban_operation_kind(
                                record["name"], record["args"]
                            ),
                            terminal_envelope=record["name"].lower() == "terminal",
                        )
                        for record in readbacks
                    )
                    for status in claimed_statuses
                )
            )
            if not status_matches:
                missing.append(f"{task_id}:readback")

    if not missing:
        return KanbanClaimDecision(rejected=False, task_ids=task_ids)
    return KanbanClaimDecision(
        rejected=True,
        task_ids=task_ids,
        missing_evidence=missing,
        message=(
            "Kanban claim rejected: current-turn mutation evidence and explicit "
            "show/list status readback are required before reporting task state."
        ),
    )


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
