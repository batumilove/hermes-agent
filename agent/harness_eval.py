"""Static / dry-run failure-case evaluator.

Parses JSONL harness-learning files, validates required fields, normalizes
list/string fields, and emits a JSON report with total/valid/invalid/cases/errors.
No model calls are made in dry-run mode.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.harness_learning import FailureType


class HarnessCaseValidationError(Exception):
    """Raised when case files cannot be loaded or no glob matches are found."""


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


def evaluate_case_files(
    paths: list[str], *, dry_run: bool = True
) -> dict[str, Any]:
    """Validate a list of JSONL case files and return a report.

    Args:
        paths: File paths or glob patterns.
        dry_run: When ``True``, only validate and report; no model calls.

    Returns:
        dict with keys ``total``, ``valid``, ``invalid``, ``cases``,
        ``errors``, and ``dry_run``.
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
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "cases": cases,
        "errors": errors,
        "dry_run": dry_run,
    }
