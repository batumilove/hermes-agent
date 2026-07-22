"""Action realization layer for canonicalizing and gating tool calls.

The realizer converts model intent into safer executable tool arguments before
registry dispatch. It is intentionally small and deterministic in v0: no model
calls, no filesystem writes, and no hidden side effects.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class RealizationAction(str, Enum):
    ALLOW = "allow"
    REPAIR = "repair"
    REJECT = "reject"
    REQUIRE_CONFIRMATION = "require_confirmation"


@dataclass(frozen=True)
class RealizationDecision:
    action: RealizationAction
    args: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    requires_evidence: bool = False

    @property
    def allows_execution(self) -> bool:
        return self.action in {RealizationAction.ALLOW, RealizationAction.REPAIR}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        return data


class ActionRealizer:
    """Pre-tool deterministic action realization.

    V0 policy:
    - canonicalize relative file paths for write_file/patch replace mode;
    - require evidence after successful file mutations;
    - map ambiguous cron "stop" wording away from destructive removal;
    - reject stale task-progress memory writes.
    """

    _STOP_RE = re.compile(r"\b(stop|disable|pause|turn\s+off)\b", re.I)
    _STALE_MEMORY_RE = re.compile(
        r"\b(PR\s*#?\d+|issue\s*#?\d+|commit\s+[0-9a-f]{7,40}|phase\s+\d+|merged|submitted|fixed|done)\b",
        re.I,
    )

    def __init__(self, *, cwd: str | Path | None = None, user_task: str | None = None) -> None:
        self.cwd = Path(cwd or Path.cwd())
        self.user_task = user_task or ""

    def realize(self, tool_name: str, args: Mapping[str, Any] | None) -> RealizationDecision:
        tool = str(tool_name or "")
        next_args = dict(args or {})

        if tool in {"write_file", "patch"}:
            return self._realize_file_mutation(tool, next_args)
        if tool == "cronjob":
            return self._realize_cronjob(next_args)
        if tool == "memory":
            return self._realize_memory(next_args)
        return RealizationDecision(RealizationAction.ALLOW, next_args)

    def _realize_file_mutation(self, tool: str, args: dict[str, Any]) -> RealizationDecision:
        path = args.get("path")
        if isinstance(path, str) and path and not _is_absolute_or_home(path):
            repaired = dict(args)
            repaired["path"] = str((self.cwd / path).resolve())
            return RealizationDecision(
                RealizationAction.REPAIR,
                repaired,
                message="canonicalized relative path before file mutation",
                requires_evidence=True,
            )
        return RealizationDecision(
            RealizationAction.ALLOW,
            args,
            requires_evidence=True,
        )

    def _realize_cronjob(self, args: dict[str, Any]) -> RealizationDecision:
        action = str(args.get("action") or "").strip().lower()
        if action == "remove":
            delete_authorized = _has_affirmative_imperative_delete_intent(self.user_task)
            if self._STOP_RE.search(self.user_task) and not delete_authorized:
                repaired = dict(args)
                repaired["action"] = "pause"
                return RealizationDecision(
                    RealizationAction.REPAIR,
                    repaired,
                    message="ambiguous stop means pause/disable, not remove/delete",
                    requires_evidence=True,
                )
            if not delete_authorized:
                return RealizationDecision(
                    RealizationAction.REJECT,
                    args,
                    message=(
                        "cron removal requires delete/remove/destroy authorization "
                        "in the current user turn; continue/proceed and prior "
                        "recommendations are not authorization"
                    ),
                    requires_evidence=True,
                )
        return RealizationDecision(RealizationAction.ALLOW, args, requires_evidence=action in {"create", "update", "pause", "resume", "remove", "run"})

    def _realize_memory(self, args: dict[str, Any]) -> RealizationDecision:
        texts: list[str] = []
        if isinstance(args.get("content"), str):
            texts.append(args["content"])
        ops = args.get("operations")
        if isinstance(ops, list):
            for op in ops:
                if isinstance(op, Mapping) and isinstance(op.get("content"), str):
                    texts.append(op["content"])
        combined = "\n".join(texts)
        if combined and self._STALE_MEMORY_RE.search(combined):
            return RealizationDecision(
                RealizationAction.REJECT,
                args,
                message="memory writes must not store stale task progress, PR/issue numbers, commit SHAs, or completed-work logs",
            )
        return RealizationDecision(RealizationAction.ALLOW, args)


def realize_tool_action(tool_name: str, args: Mapping[str, Any] | None, *, cwd: str | Path | None = None, user_task: str | None = None) -> RealizationDecision:
    return ActionRealizer(cwd=cwd, user_task=user_task).realize(tool_name, args)


def rejection_result(decision: RealizationDecision) -> str:
    return json.dumps(
        {
            "error": f"Action realization rejected tool call: {decision.message}",
            "action_realization": decision.to_dict(),
        },
        ensure_ascii=False,
    )


def _is_absolute_or_home(path: str) -> bool:
    return path.startswith("~/") or Path(path).is_absolute() or bool(re.match(r"^[A-Za-z]:[/\\]", path))


def _has_affirmative_imperative_delete_intent(text: str) -> bool:
    """Accept only an unquoted, non-conditional delete command in this turn."""

    unquoted = re.sub(r"(['\"`]).*?\1", " ", str(text or ""), flags=re.DOTALL)
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", unquoted):
        if not sentence.strip() or "?" in sentence:
            continue
        if re.search(
            r"\b(?:if|unless|hypothetically|would|could|might|should|can you|how to)\b",
            sentence,
            re.I,
        ):
            continue
        if re.search(
            r"\b(?:do not|don't|never|not)\b[^.!?;]{0,60}"
            r"\b(?:delete|remove|destroy)\b",
            sentence,
            re.I,
        ):
            continue
        if not re.search(r"\b(?:cron(?:job)?|job|schedule|timer)\b", sentence, re.I):
            continue
        if re.search(
            r"(?:^|[.!;]\s*|\band\s+)(?:please\s+)?"
            r"(?:go\s+ahead\s+and\s+)?"
            r"(?:permanently\s+delete|delete|remove|destroy)\b",
            sentence.strip(),
            re.I,
        ):
            return True
    return False
