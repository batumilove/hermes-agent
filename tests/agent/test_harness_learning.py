"""Harness Learning v0 regression tests.

These tests codify the first Life-Harness-style runtime structures:
- failure taxonomy records that can become regression fixtures,
- an action-realization seam around tool calls,
- side-effect evidence requirements before success claims.
"""

from __future__ import annotations

import json

from agent.harness_learning import (
    FailureDiagnosis,
    FailureType,
    HarnessPatchKind,
    RegressionTask,
    SideEffectEvidenceRegulator,
    _tool_result_succeeded,
    diagnose_repeated_tool_failure,
)
from agent.action_realization import ActionRealizer, RealizationAction


def test_failure_diagnosis_serializes_stable_taxonomy_with_regression_task():
    diagnosis = FailureDiagnosis(
        failure_type=FailureType.ACTION_REALIZATION,
        confidence="high",
        evidence=["cronjob(action='remove') was proposed for user wording 'stop cron'"],
        root_cause_hypothesis="The model mapped an ambiguous stop request to destructive removal.",
        suggested_harness_patch={
            "kind": HarnessPatchKind.ACTION_REALIZATION.value,
            "description": "Map stop/disable wording to pause unless delete/remove is explicit.",
        },
        regression_task=RegressionTask(
            id="cron-stop-means-pause",
            prompt="Stop the daily backup cron",
            expected_behavior=["list jobs first", "pause/disable matching job", "do not remove"],
            forbidden_behavior=["cronjob remove without explicit delete wording"],
        ),
        regression_risk="low",
    )

    payload = diagnosis.to_dict()

    assert payload["failure_type"] == "action_realization"
    assert payload["suggested_harness_patch"]["kind"] == "action_realization"
    assert payload["regression_task"]["id"] == "cron-stop-means-pause"
    assert json.loads(diagnosis.to_json())["confidence"] == "high"


def test_diagnose_repeated_tool_failure_creates_patchable_trajectory_failure():
    diagnosis = diagnose_repeated_tool_failure(
        tool_name="terminal",
        args_hash="abc123",
        error_preview="No such file or directory: /missing",
        count=3,
    )

    assert diagnosis.failure_type is FailureType.TRAJECTORY_DEGRADATION
    assert diagnosis.suggested_harness_patch["kind"] == HarnessPatchKind.REGULATOR.value
    assert "same failing terminal call" in diagnosis.regression_task.forbidden_behavior[0]
    assert "No such file" in diagnosis.evidence[0]


def test_action_realizer_canonicalizes_file_paths_without_executing_tool(tmp_path):
    realizer = ActionRealizer(cwd=tmp_path)

    decision = realizer.realize("write_file", {"path": "notes/out.md", "content": "ok"})

    assert decision.action is RealizationAction.REPAIR
    assert decision.args["path"] == str(tmp_path / "notes" / "out.md")
    assert decision.requires_evidence is True
    assert "canonicalized relative path" in decision.message


def test_action_realizer_maps_stop_cron_to_pause_and_rejects_remove_without_explicit_delete():
    realizer = ActionRealizer(user_task="Stop the daily backup cron")

    repaired = realizer.realize("cronjob", {"action": "remove", "job_id": "job-1"})

    assert repaired.action is RealizationAction.REPAIR
    assert repaired.args["action"] == "pause"
    assert "stop means pause" in repaired.message

    explicit = ActionRealizer(user_task="Delete the daily backup cron")
    allowed = explicit.realize("cronjob", {"action": "remove", "job_id": "job-1"})
    assert allowed.action is RealizationAction.ALLOW


def test_action_realizer_rejects_memory_task_progress_as_stale_context():
    realizer = ActionRealizer()

    decision = realizer.realize(
        "memory",
        {"action": "add", "target": "memory", "content": "PR #123 merged and phase 2 is done"},
    )

    assert decision.action is RealizationAction.REJECT
    assert "task progress" in decision.message


def test_side_effect_evidence_regulator_requires_handles_before_success_claims():
    regulator = SideEffectEvidenceRegulator()
    regulator.observe_tool_result("write_file", {"path": "/tmp/x.md"}, '{"bytes_written": 2}')

    missing = regulator.evaluate_final_response("Done, the file was updated successfully.")
    assert missing.requires_evidence is False  # write_file result itself is evidence

    regulator = SideEffectEvidenceRegulator()
    claimed = regulator.evaluate_final_response("I sent the message and created the GitHub issue successfully.")
    assert claimed.requires_evidence is True
    assert "send_message" in claimed.missing_evidence_for
    assert "github" in claimed.missing_evidence_for


def test_side_effect_evidence_regulator_flags_cron_upload_deploy_and_delete_claims():
    regulator = SideEffectEvidenceRegulator()

    claimed = regulator.evaluate_final_response(
        "I scheduled the cron, uploaded the artifact, deployed the service, and deleted the old job."
    )

    assert claimed.requires_evidence is True
    assert claimed.missing_evidence_for == ["cronjob", "upload", "deploy", "delete"]


def test_status_heading_does_not_hide_earlier_action_clause():
    regulator = SideEffectEvidenceRegulator()

    claimed = regulator.evaluate_final_response(
        "Deployed the canary, and verification: pass"
    )

    assert claimed.requires_evidence is True
    assert "deploy" in claimed.missing_evidence_for


def test_execute_code_explicit_null_exit_code_is_not_success():
    assert _tool_result_succeeded(
        "execute_code",
        {"status": "success", "output": "ok", "exit_code": None},
    ) is False
    assert _tool_result_succeeded(
        "execute_code",
        {"status": "success", "output": "ok"},
    ) is True


def test_side_effect_evidence_footer_scans_current_turn_tool_messages_only():
    from agent.harness_learning import build_side_effect_evidence_footer

    messages = [
        {"role": "user", "content": "old turn"},
        {"role": "tool", "name": "send_message", "content": '{"message_id":1}'},
        {"role": "assistant", "content": "Sent."},
        {"role": "user", "content": "new turn"},
    ]

    footer = build_side_effect_evidence_footer(
        messages,
        "I sent the message and created the GitHub issue.",
    )

    assert "Side-effect evidence regulator" in footer
    assert "send_message" in footer
    assert "github" in footer

    messages.append({"role": "tool", "name": "send_message", "content": '{"message_id":2}'})
    messages.append({"role": "tool", "name": "github_issue", "content": '{"url":"https://github.com/o/r/issues/1"}'})
    assert build_side_effect_evidence_footer(messages, "I sent the message and created the GitHub issue.") == ""
