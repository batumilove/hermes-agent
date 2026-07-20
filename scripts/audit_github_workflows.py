#!/usr/bin/env python3
"""Audit GitHub workflow YAML for small-batch hardening candidates.

Usage:
  python scripts/audit_github_workflows.py [repo-root]

The scanner separates actionable findings from informational workflow-call
limitations so future hardening passes do not chase invalid GitHub Actions
syntax. In particular:

- jobs that call reusable workflows with top-level ``uses:`` cannot use the
  same runnable-job keyword set as ``runs-on``/``steps`` jobs, so missing
  ``timeout-minutes`` on those wrapper jobs is informational.
- top-level ``concurrency`` on workflows that can be invoked via
  ``workflow_call`` needs a per-call discriminator; a generic group can cancel
  sibling reusable jobs from the same orchestrator. Those are reported as
  skipped/cautionary, not actionable.

Checks are intentionally conservative and evidence-oriented:
- direct GitHub expression interpolation inside real ``run:`` blocks
- external ``steps[*].uses`` values not pinned to a full 40-char SHA
- workflows using ``pull_request_target``
- workflows missing top-level ``permissions``
- malformed workflow YAML, including duplicate mapping keys
- runnable jobs missing ``timeout-minutes``
- invalid ``timeout-minutes`` on reusable workflow-call wrapper jobs
- standalone workflows missing top-level ``concurrency``
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - setup issue, not logic
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

SHA40 = re.compile(r"^[0-9a-f]{40}$")
EXPR = "${{"


ACTIONABLE_KEYS = (
    "yaml_parse_errors",
    "run_expression_hits",
    "unpinned_uses",
    "pull_request_target",
    "missing_top_level_permissions",
    "runnable_jobs_without_timeout",
    "invalid_timeout_on_workflow_callers",
    "standalone_workflows_without_concurrency",
)


class UniqueKeyLoader(yaml.BaseLoader):
    """BaseLoader variant that rejects duplicate YAML mapping keys."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, *, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def load_workflow(path: Path) -> Any:
    # BaseLoader avoids YAML 1.1 surprises such as `on:` becoming boolean True.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def event_names(events: Any) -> set[str]:
    if isinstance(events, str):
        return {events}
    if isinstance(events, list):
        return {str(item) for item in events}
    if isinstance(events, dict):
        return {str(key) for key in events}
    return set()


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    workflow_dir = root / ".github" / "workflows"
    findings: dict[str, list[dict[str, str]]] = {
        "yaml_parse_errors": [],
        "run_expression_hits": [],
        "unpinned_uses": [],
        "pull_request_target": [],
        "missing_top_level_permissions": [],
        "runnable_jobs_without_timeout": [],
        "invalid_timeout_on_workflow_callers": [],
        "standalone_workflows_without_concurrency": [],
        "reusable_call_jobs_without_timeout": [],
        "workflow_call_workflows_without_concurrency": [],
    }

    for path in sorted(workflow_dir.glob("*.y*ml")):
        rel = str(path.relative_to(root))
        try:
            data = load_workflow(path)
        except yaml.YAMLError as exc:
            findings["yaml_parse_errors"].append({"file": rel, "error": str(exc)})
            continue
        doc = as_dict(data)

        if "permissions" not in doc:
            findings["missing_top_level_permissions"].append({"file": rel})

        events = doc.get("on")
        names = event_names(events)
        has_workflow_call = "workflow_call" in names
        if "pull_request_target" in names:
            findings["pull_request_target"].append({"file": rel})

        if "concurrency" not in doc:
            if has_workflow_call:
                findings["workflow_call_workflows_without_concurrency"].append(
                    {
                        "file": rel,
                        "reason": "workflow_call concurrency needs a per-call discriminator to avoid cancelling sibling reusable jobs",
                    }
                )
            else:
                findings["standalone_workflows_without_concurrency"].append({"file": rel})

        jobs = as_dict(doc.get("jobs"))
        for job_name, job in jobs.items():
            jobd = as_dict(job)
            is_reusable_call = "uses" in jobd
            has_timeout = "timeout-minutes" in jobd
            if not has_timeout:
                key = (
                    "reusable_call_jobs_without_timeout"
                    if is_reusable_call
                    else "runnable_jobs_without_timeout"
                )
                finding = {"file": rel, "job": str(job_name)}
                if is_reusable_call:
                    finding["reason"] = "top-level uses reusable workflow callers do not support timeout-minutes; set timeouts inside the called workflow"
                findings[key].append(finding)
            elif is_reusable_call:
                findings["invalid_timeout_on_workflow_callers"].append(
                    {"file": rel, "job": str(job_name)}
                )

            for idx, step in enumerate(as_list(jobd.get("steps")), 1):
                stepd = as_dict(step)
                step_name = str(stepd.get("name") or f"step {idx}")
                run = stepd.get("run")
                if isinstance(run, str) and EXPR in run:
                    findings["run_expression_hits"].append(
                        {"file": rel, "job": str(job_name), "step": step_name}
                    )

                uses = stepd.get("uses")
                if not isinstance(uses, str) or uses.startswith("./"):
                    continue
                if "@" not in uses:
                    findings["unpinned_uses"].append(
                        {"file": rel, "job": str(job_name), "step": step_name, "uses": uses}
                    )
                    continue
                ref = uses.rsplit("@", 1)[1]
                if not SHA40.fullmatch(ref):
                    findings["unpinned_uses"].append(
                        {"file": rel, "job": str(job_name), "step": step_name, "uses": uses}
                    )

    print(json.dumps(findings, indent=2, sort_keys=True))
    return 1 if any(findings[key] for key in ACTIONABLE_KEYS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
