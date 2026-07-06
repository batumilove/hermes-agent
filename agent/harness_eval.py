"""Static / dry-run failure-case evaluator and trace scorer.

Parses JSONL harness-learning files, validates required fields, normalizes
list/string fields, and emits a JSON report with total/valid/invalid/cases/errors.
When ``--trace`` is provided, JSONL execution traces are scored deterministically
against the loaded cases. No model calls are made.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.harness_learning import FailureType, SideEffectEvidenceRegulator


class HarnessCaseValidationError(Exception):
    """Raised when case files cannot be loaded or no glob matches are found."""


class HarnessTraceValidationError(Exception):
    """Raised when trace files cannot be loaded or no glob matches are found."""


@dataclass(frozen=True)
class _ValidationError:
    source: str
    message: str


@dataclass(frozen=True)
class _CaseRecord:
    id: str
    failure_type: str
    prompt: str
    expected_behavior: list[str] = field(default_factory=list)
    forbidden_behavior: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    regression_risk: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "failure_type": self.failure_type,
            "prompt": self.prompt,
            "expected_behavior": self.expected_behavior,
            "forbidden_behavior": self.forbidden_behavior,
            "required_evidence": self.required_evidence,
            "regression_risk": self.regression_risk,
            "source": self.source,
        }


def _normalize_list(value: Any, *, allow_string: bool = True) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        if allow_string:
            return [value]
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and item != ""]
    return [str(value)]


def _has_any(behaviors: list[str]) -> bool:
    return any(item.strip() for item in behaviors)


def _is_valid_failure_type(value: str) -> bool:
    return value in {member.value for member in FailureType}


def _validate_case(
    raw: dict[str, Any], source: str, seen_ids: set[str]
) -> tuple[_CaseRecord | None, list[_ValidationError]]:
    errors: list[_ValidationError] = []
    case_id = raw.get("id")
    if not case_id or not str(case_id).strip():
        errors.append(_ValidationError(source=source, message="missing required field: id"))
        return None, errors
    case_id = str(case_id)

    failure_type = raw.get("failure_type")
    if not failure_type or not str(failure_type).strip():
        errors.append(_ValidationError(source=source, message="missing required field: failure_type"))
    elif not _is_valid_failure_type(str(failure_type)):
        errors.append(
            _ValidationError(
                source=source,
                message=f"invalid failure_type '{failure_type}'; must be one of {[m.value for m in FailureType]}",
            )
        )

    prompt = raw.get("prompt")
    if not prompt or not str(prompt).strip():
        errors.append(_ValidationError(source=source, message="missing required field: prompt"))

    expected_behavior = _normalize_list(raw.get("expected_behavior"))
    forbidden_behavior = _normalize_list(raw.get("forbidden_behavior"))
    required_evidence = _normalize_list(raw.get("required_evidence"))
    regression_risk = str(raw.get("regression_risk", ""))

    if not _has_any(expected_behavior) and not _has_any(forbidden_behavior):
        errors.append(
            _ValidationError(
                source=source,
                message="expected_behavior or forbidden_behavior must contain at least one non-empty entry",
            )
        )

    if case_id in seen_ids:
        errors.append(_ValidationError(source=source, message=f"duplicate id '{case_id}'"))
    else:
        seen_ids.add(case_id)

    if errors:
        return None, errors

    return (
        _CaseRecord(
            id=case_id,
            failure_type=str(failure_type),
            prompt=str(prompt),
            expected_behavior=expected_behavior,
            forbidden_behavior=forbidden_behavior,
            required_evidence=required_evidence,
            regression_risk=regression_risk,
            source=source,
        ),
        [],
    )


class HarnessCaseLoader:
    """Load and normalize harness case JSONL files."""

    def load_paths(self, paths: list[str]) -> Any:
        """Yield (path, line_number, raw_dict) tuples for each JSONL line.

        ``paths`` may contain glob patterns. If a glob pattern matches no
        files, ``HarnessCaseValidationError`` is raised.

        For lines that are not valid JSON, ``raw`` is ``None`` and the caller
        is responsible for recording the parse error.
        """
        for pattern in paths:
            expanded = glob.glob(pattern)
            if not expanded:
                if "*" in pattern or "?" in pattern or "[" in pattern:
                    raise HarnessCaseValidationError(
                        f"glob pattern '{pattern}' matched no files"
                    )
                expanded = [pattern]
            for path_str in expanded:
                path = Path(path_str)
                if not path.exists():
                    raise HarnessCaseValidationError(f"case file not found: {path}")
                with path.open(encoding="utf-8") as f:
                    for line_number, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            yield (path, line_number, None)
                            continue
                        yield (path, line_number, raw)


@dataclass(frozen=True)
class TraceRecord:
    """Normalized trace row keyed by ``case_id``.

    A trace describes one execution trajectory for a harness case. It is
    intentionally minimal: the tool calls and final response are the only
    evidence needed for deterministic scoring.
    """

    case_id: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_response: str = ""
    source: str = ""

    @classmethod
    def from_raw(
        cls, raw: dict[str, Any], *, source: str
    ) -> tuple[TraceRecord | None, list[str]]:
        """Parse a raw trace dict. Returns (record, errors)."""
        errors: list[str] = []
        case_id = raw.get("case_id")
        if not case_id or not str(case_id).strip():
            errors.append("missing required field: case_id")
            return None, errors
        case_id = str(case_id)

        tool_calls = raw.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            errors.append("tool_calls must be a list")
            tool_calls = []
        else:
            normalized: list[dict[str, Any]] = []
            for idx, item in enumerate(tool_calls):
                if not isinstance(item, dict):
                    errors.append(f"tool_calls[{idx}] must be an object")
                    continue
                normalized.append(item)
            tool_calls = normalized

        final_response = str(raw.get("final_response", ""))

        if errors:
            return None, errors
        return cls(case_id=case_id, tool_calls=tool_calls, final_response=final_response, source=source), []

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "tool_calls": self.tool_calls,
            "final_response": self.final_response,
            "source": self.source,
        }


class HarnessTraceLoader:
    """Load and normalize JSONL trace files."""

    def load_paths(self, paths: list[str]) -> Any:
        """Yield (path, line_number, raw_dict) tuples for each JSONL line.

        ``paths`` may contain glob patterns. If a glob pattern matches no
        files, ``HarnessTraceValidationError`` is raised.
        """
        for pattern in paths:
            expanded = glob.glob(pattern)
            if not expanded:
                if "*" in pattern or "?" in pattern or "[" in pattern:
                    raise HarnessTraceValidationError(
                        f"glob pattern '{pattern}' matched no files"
                    )
                expanded = [pattern]
            for path_str in expanded:
                path = Path(path_str)
                if not path.exists():
                    raise HarnessTraceValidationError(f"trace file not found: {path}")
                with path.open(encoding="utf-8") as f:
                    for line_number, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            yield (path, line_number, None)
                            continue
                        yield (path, line_number, raw)


class HarnessTraceScorer:
    """Score deterministic trace records against a set of harness cases."""

    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self._case_index: dict[str, dict[str, Any]] = {}
        for case in cases:
            case_id = str(case.get("id") or "")
            if case_id:
                self._case_index[case_id] = case

    def score_traces(self, traces: list[dict[str, Any]]) -> dict[str, Any]:
        """Score a list of trace dicts and return an aggregate report.

        The returned report has keys ``scored``, ``passed``, ``failed``,
        ``warnings``, and ``results``. Each result has ``case_id``, ``status``
        (``pass`` / ``fail`` / ``warn``), ``evidence`` (key/value handles),
        ``failures``, and ``warnings``.
        """
        results: list[dict[str, Any]] = []
        passed = 0
        failed = 0
        warnings = 0

        for trace in traces:
            result = self._score_trace(trace)
            results.append(result)
            if result["status"] == "pass":
                passed += 1
            elif result["status"] == "fail":
                failed += 1
            if result["warnings"]:
                warnings += 1

        return {
            "scored": len(results),
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "results": results,
        }

    def _score_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        case_id = str(trace.get("case_id") or "")
        case = self._case_index.get(case_id)
        if not case:
            return {
                "case_id": case_id,
                "status": "fail",
                "evidence": {},
                "failures": [f"no case found for case_id '{case_id}'"],
                "warnings": [],
            }

        tool_calls = trace.get("tool_calls", []) if isinstance(trace, dict) else []
        final_response = str(trace.get("final_response", "") if isinstance(trace, dict) else "")
        failures: list[str] = []
        warnings_list: list[str] = []
        evidence: dict[str, Any] = {}
        observed_chunks: list[str] = []

        regulator = SideEffectEvidenceRegulator()
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            name = call.get("name")
            args = call.get("args") or {}
            result = call.get("result")
            if name:
                str_name = str(name)
                regulator.observe_tool_result(str_name, args, result)
                observed_chunks.extend([str_name, _compact_text(args), _compact_text(result)])
                # Treat the tool name plus the result as a keyed evidence handle.
                evidence[str_name] = result
                # Flatten primitive keys from the result for easier required-evidence matching.
                if isinstance(result, dict):
                    for key, value in result.items():
                        if isinstance(value, (str, int, float, bool)):
                            evidence[key] = value
                evidence.update(_extract_evidence_handles(args))
                evidence.update(_extract_evidence_handles(result))

        # Forbidden behavior: any forbidden tool or final-response substring.
        forbidden_behavior = _normalize_list(case.get("forbidden_behavior"))
        for forbidden in forbidden_behavior:
            if forbidden.strip():
                failure = self._check_forbidden(forbidden, tool_calls, final_response, evidence)
                if failure:
                    failures.append(failure)

        # Required evidence: required evidence tokens must appear in evidence or response.
        required_evidence = _normalize_list(case.get("required_evidence"))
        missing_evidence = self._find_missing_evidence(required_evidence, evidence, final_response, observed_chunks)
        if missing_evidence:
            failures.append(f"missing required evidence: {', '.join(missing_evidence)}")

        # Unsupported side-effect claim: final response claims side effects without tool evidence.
        decision = regulator.evaluate_final_response(final_response)
        if decision.requires_evidence:
            failures.append(
                f"unsupported side-effect claim: {', '.join(decision.missing_evidence_for)}"
            )

        # Expected behavior: if not machine-checkable, warn rather than fail.
        expected_behavior = _normalize_list(case.get("expected_behavior"))
        uncheckable = self._find_uncheckable_expected_behavior(expected_behavior)
        if uncheckable:
            warnings_list.append(f"expected behavior not machine-checkable: {', '.join(uncheckable)}")

        status = "fail" if failures else ("warn" if warnings_list else "pass")
        return {
            "case_id": case_id,
            "status": status,
            "evidence": evidence,
            "failures": failures,
            "warnings": warnings_list,
        }

    def _check_forbidden(
        self,
        forbidden: str,
        tool_calls: list[Any],
        final_response: str,
        evidence: dict[str, Any],
    ) -> str | None:
        lower = forbidden.lower()
        # Check final response substring.
        if lower in final_response.lower():
            return f"forbidden final response mentions '{forbidden}'"
        # Check tool name or "tool args" pattern (e.g., "cronjob remove" means name + action).
        parts = lower.split()
        if len(parts) >= 2:
            tool_name = parts[0]
            action = parts[1]
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                if str(call.get("name", "")).lower() == tool_name:
                    args = call.get("args") or {}
                    if isinstance(args, dict):
                        if str(args.get("action", "")).lower() == action:
                            return f"forbidden tool action '{forbidden}'"
        else:
            # Single-word forbidden token: match tool name or evidence key.
            if lower in {str(c.get("name", "")).lower() for c in tool_calls if isinstance(c, dict)}:
                return f"forbidden tool '{forbidden}'"
            if lower in {k.lower() for k in evidence}:
                return f"forbidden evidence key '{forbidden}'"
        return None

    def _find_missing_evidence(
        self,
        required_evidence: list[str],
        evidence: dict[str, Any],
        final_response: str,
        observed_chunks: list[str],
    ) -> list[str]:
        missing: list[str] = []
        evidence_text = " ".join(_compact_text(v) for v in evidence.values())
        evidence_keys = " ".join(evidence.keys())
        search_pool = f"{evidence_keys} {evidence_text} {' '.join(observed_chunks)} {final_response}".lower()
        for token in required_evidence:
            if not token.strip():
                continue
            if _evidence_requirement_satisfied(token, search_pool):
                continue
            missing.append(token)
        return missing

    def _find_uncheckable_expected_behavior(self, expected_behavior: list[str]) -> list[str]:
        """Return expected behavior entries that are not machine-checkable.

        An expected behavior is considered checkable when it references a
        concrete tool name, an action token, or an evidence key that can be
        matched against the trace. Abstract qualitative statements (e.g.
        "explain carefully", "think step by step") cannot be verified
        deterministically and are reported as warnings.
        """
        uncheckable: list[str] = []
        for entry in expected_behavior:
            if not entry.strip():
                continue
            lower = entry.lower()
            checkable = (
                any(tool in lower for tool in self._known_tool_names())
                or any(token in lower for token in ("action=", "action:", "job_id", "issue", "url", "status"))
                or lower.startswith(("use ", "call ", "create ", "pause", "disable", "enable", "remove", "delete"))
            )
            if not checkable:
                uncheckable.append(entry)
        return uncheckable

    def _known_tool_names(self) -> set[str]:
        return {
            "cronjob",
            "github",
            "send_message",
            "write_file",
            "patch",
            "browser_click",
            "browser_type",
            "browser_press",
            "image_generate",
            "text_to_speech",
            "terminal",
            "process",
            "mcp_executor_execute",
        }


def evaluate_case_files(
    paths: list[str], *, dry_run: bool = True, trace: list[str] | None = None
) -> dict[str, Any]:
    """Validate a list of JSONL case files and optionally score a trace file.

    Args:
        paths: File paths or glob patterns for case JSONL files.
        dry_run: When ``True``, only validate and report; no model calls.
        trace: Optional file path(s) or glob pattern(s) for JSONL trace files.

    Returns:
        dict with keys ``total``, ``valid``, ``invalid``, ``cases``,
        ``errors``, ``dry_run``, and optionally ``trace_scoring``.
    """
    loader = HarnessCaseLoader()
    cases: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    total = 0

    try:
        items = list(loader.load_paths(paths))
    except HarnessCaseValidationError as exc:
        return {
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "cases": [],
            "errors": [{"source": "", "message": str(exc)}],
            "dry_run": dry_run,
        }

    for path, line_number, raw in items:
        total += 1
        source = f"{path}:{line_number}"
        if raw is None:
            errors.append({"source": source, "message": "invalid JSONL"})
            continue
        if not isinstance(raw, dict):
            errors.append(
                {"source": source, "message": "each JSONL line must be a JSON object"}
            )
            continue
        record, record_errors = _validate_case(raw, source, seen_ids)
        if record_errors:
            errors.extend({"source": e.source, "message": e.message} for e in record_errors)
        if record is not None:
            cases.append(record.to_dict())

    valid = len(cases)
    invalid = total - valid
    report: dict[str, Any] = {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "cases": cases,
        "errors": errors,
        "dry_run": dry_run,
    }

    if trace:
        trace_report = _score_trace_files(trace, cases)
        report["trace_scoring"] = trace_report

    return report


def _score_trace_files(
    trace_paths: list[str], cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Load trace files and score them against the loaded cases."""
    loader = HarnessTraceLoader()
    traces: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        items = list(loader.load_paths(trace_paths))
    except HarnessTraceValidationError as exc:
        return {
            "scored": 0,
            "passed": 0,
            "failed": 0,
            "warnings": 0,
            "results": [],
            "errors": [str(exc)],
        }

    for path, line_number, raw in items:
        source = f"{path}:{line_number}"
        if raw is None:
            errors.append(f"{source}: invalid JSONL")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{source}: each JSONL line must be a JSON object")
            continue
        record, record_errors = TraceRecord.from_raw(raw, source=source)
        if record_errors:
            errors.extend(f"{source}: {message}" for message in record_errors)
            continue
        if record is not None:
            traces.append(record.to_dict())

    scorer = HarnessTraceScorer(cases)
    report = scorer.score_traces(traces)
    if errors:
        report["errors"] = errors
    return report


def _evidence_requirement_satisfied(requirement: str, search_pool: str) -> bool:
    """Return True if a human-readable evidence requirement is observed.

    Requirements in fixtures are prose ("issue URL or number", "deployment
    status or version/hash"). For deterministic scoring, split on loose
    boolean separators and accept any concrete token that appears in the
    observed trace text/handles.
    """
    requirement_lower = requirement.lower().replace("/", " or ")
    for raw in requirement_lower.replace(",", " or ").split(" or "):
        token = raw.strip()
        if not token:
            continue
        words = [word for word in token.replace("-", " ").split() if len(word) > 1]
        if not words:
            continue
        if token in {"id", "url", "number", "status", "job id", "message id", "issue number", "issue url"}:
            if all(word in search_pool for word in words):
                return True
            continue
        if len(words) > 1:
            if all(word in search_pool for word in words):
                return True
            continue
        if words[0] in search_pool:
            return True
    return requirement.lower() in search_pool


def _extract_evidence_handles(value: Any) -> dict[str, str]:
    """Extract common handle/status fields from nested args/results."""
    handles: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {
                "id",
                "job_id",
                "message_id",
                "issue_url",
                "issue_number",
                "url",
                "status",
                "exit_code",
                "sha",
                "commit",
                "version",
            } and item not in (None, ""):
                handles[lowered] = str(item)
            handles.update(_extract_evidence_handles(item))
    elif isinstance(value, list):
        for item in value:
            handles.update(_extract_evidence_handles(item))
    return handles


def _compact_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)
