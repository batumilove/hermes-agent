import json

import pytest

from agent.side_effect_evidence import (
    build_side_effect_evidence_footer,
    tool_result_succeeded,
)


WARNING = "Side-effect evidence regulator"


def _tool(name: str, result: dict) -> dict:
    return {"role": "tool", "name": name, "content": json.dumps(result)}


def _warns(response: str, messages=None) -> bool:
    return WARNING in build_side_effect_evidence_footer(
        messages or [{"role": "user", "content": "do it"}], response
    )


def test_plain_status_shaped_deployment_claim_requires_evidence():
    assert _warns("Deployed to production, status: PASS")


def test_markdown_parity_heading_is_descriptive_not_a_fresh_claim():
    assert not _warns("## 7. Merged source and deployed runtime parity — PASS")


@pytest.mark.parametrize(
    "response",
    [
        "Earlier, the service was deployed to production.",
        "Current state: the service is deployed to production.",
        "Inspection shows the runtime was deployed before this turn.",
        "The service was not deployed.",
    ],
)
def test_non_fresh_deployment_descriptions_do_not_require_evidence(response):
    assert not _warns(response)


@pytest.mark.parametrize(
    "response",
    [
        (
            "Activation-record reconciliation: the canonical scheduler record "
            "is enabled and scheduled; no separate activation record is in scope."
        ),
        "Production cron: olah-health-monitor is enabled and scheduled every 30 minutes.",
    ],
)
def test_coordinated_present_state_cron_description_does_not_require_evidence(response):
    assert not _warns(response)


def test_coordinated_sentence_with_fresh_first_person_cron_action_requires_evidence():
    response = "I confirmed the scheduler is enabled and scheduled the cron job."

    assert _warns(response)


@pytest.mark.parametrize(
    "response",
    [
        "The operator is authenticated and scheduled the cron job.",
        "The operator is ready and deployed the service.",
    ],
)
def test_coordinated_non_first_person_actions_require_evidence(response):
    assert _warns(response)


def test_successful_terminal_envelope_is_current_turn_evidence():
    messages = [
        {"role": "user", "content": "deploy"},
        _tool("terminal", {"output": "ok", "exit_code": 0, "error": None}),
    ]
    assert not _warns("Deployed successfully.", messages)


def test_trusted_predecoration_success_is_current_turn_evidence():
    messages = [
        {"role": "user", "content": "deploy"},
        {
            "role": "tool",
            "name": "terminal",
            "content": (
                json.dumps({"output": "ok", "exit_code": 0, "error": None})
                + "\n\n[Subdirectory context discovered: .hermes/AGENTS.md]\n"
                + "repository instructions"
            ),
            "_side_effect_evidence_succeeded": True,
        },
    ]
    assert not _warns("Deployed successfully.", messages)


def test_trusted_predecoration_failure_cannot_be_overridden_by_content():
    messages = [
        {"role": "user", "content": "deploy"},
        {
            "role": "tool",
            "name": "terminal",
            "content": json.dumps({"output": "ok", "exit_code": 0, "error": None}),
            "_side_effect_evidence_succeeded": False,
        },
    ]
    assert _warns("Deployed successfully.", messages)


def test_decorated_result_without_trusted_predecoration_evidence_fails_closed():
    messages = [
        {"role": "user", "content": "deploy"},
        {
            "role": "tool",
            "name": "terminal",
            "content": (
                json.dumps({"output": "ok", "exit_code": 0, "error": None})
                + "\n\n[Subdirectory context discovered: .hermes/AGENTS.md]\n"
                + "repository instructions"
            ),
        },
    ]
    assert _warns("Deployed successfully.", messages)


def test_non_boolean_predecoration_marker_is_not_trusted():
    messages = [
        {"role": "user", "content": "deploy"},
        {
            "role": "tool",
            "name": "terminal",
            "content": "not a structured result",
            "_side_effect_evidence_succeeded": "true",
        },
    ]
    assert _warns("Deployed successfully.", messages)


def test_predecoration_verdict_fails_closed_for_cyclic_result():
    cyclic = {}
    cyclic["self"] = cyclic
    assert not tool_result_succeeded("process", cyclic)


@pytest.mark.parametrize("flag", ["success", "ok"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_non_boolean_success_flags_fail_closed_even_with_positive_handle(flag, value):
    result = {flag: value, "status": "success", "job_id": "j-1"}
    assert not tool_result_succeeded("cronjob", result)


def test_successful_execute_code_result_is_current_turn_evidence():
    messages = [
        {"role": "user", "content": "deploy"},
        _tool("execute_code", {"status": "success", "exit_code": 0, "output": "ok"}),
    ]
    assert not _warns("Deployed successfully.", messages)


def test_nonzero_execute_code_exit_fails_closed():
    messages = [
        {"role": "user", "content": "deploy"},
        _tool("execute_code", {"status": "success", "exit_code": 9, "output": "failed"}),
    ]
    assert _warns("Deployed successfully.", messages)


def test_prior_turn_evidence_does_not_satisfy_current_claim():
    messages = [
        {"role": "user", "content": "old task"},
        _tool("terminal", {"output": "ok", "exit_code": 0, "error": None}),
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "new task"},
    ]
    assert _warns("Deployed successfully.", messages)


@pytest.mark.parametrize(
    ("response", "missing_kind"),
    [
        ("I sent the message successfully.", "send_message"),
        ("I created the GitHub issue.", "github"),
        ("I scheduled the cron job.", "cronjob"),
        ("Uploaded successfully.", "upload"),
        ("Deleted the remote artifact.", "delete"),
    ],
)
def test_other_side_effect_claims_fail_closed(response, missing_kind):
    footer = build_side_effect_evidence_footer(
        [{"role": "user", "content": "do it"}], response
    )
    assert WARNING in footer
    assert missing_kind in footer


@pytest.mark.parametrize(
    ("response", "tool_name", "result"),
    [
        ("I sent the message successfully.", "send_message", {"id": "m-1", "error": None}),
        ("I created the GitHub issue.", "github_create_issue", {"url": "https://example.invalid/1"}),
        ("I scheduled the cron job.", "cronjob", {"success": True, "job_id": "j-1"}),
        ("Uploaded successfully.", "image_generate", {"image": "/tmp/image.png"}),
        ("Deleted the remote artifact.", "browser_click", {"success": True}),
        (
            "Deployed successfully.",
            "mcp__executor__execute",
            {"status": "success", "executionId": "exec-1"},
        ),
    ],
)
def test_matching_current_turn_tool_result_satisfies_claim(response, tool_name, result):
    messages = [
        {"role": "user", "content": "do it"},
        _tool(tool_name, result),
    ]
    assert not _warns(response, messages)


@pytest.mark.parametrize(
    ("response", "tool_name"),
    [
        ("I sent the message successfully.", "send_message"),
        ("I created the GitHub issue.", "github_create_issue"),
        ("I scheduled the cron job.", "cronjob"),
        ("Uploaded successfully.", "image_generate"),
        ("Deleted the remote artifact.", "browser_click"),
        ("Deployed successfully.", "process"),
    ],
)
def test_empty_nonexecution_result_is_not_evidence(response, tool_name):
    messages = [
        {"role": "user", "content": "do it"},
        _tool(tool_name, {}),
    ]
    assert _warns(response, messages)


@pytest.mark.parametrize("tool_name", ["process", "mcp__executor__execute"])
def test_nonzero_child_exit_fails_closed_for_generic_execution_tools(tool_name):
    messages = [
        {"role": "user", "content": "deploy"},
        _tool(
            tool_name,
            {"status": "success", "exit_code": 9, "executionId": "exec-1"},
        ),
    ]
    assert _warns("Deployed successfully.", messages)


def test_nonserializable_tool_result_fails_closed_without_skipping_regulator():
    messages = [
        {"role": "user", "content": "deploy"},
        {
            "role": "tool",
            "name": "process",
            "content": {"status": "success", "output": b"Traceback (most recent call last)"},
        },
    ]
    assert _warns("Deployed successfully.", messages)


@pytest.mark.parametrize(
    "response",
    [
        "I removed the unused import.",
        "I deleted the dead code path.",
    ],
)
def test_routine_code_edit_prose_is_not_an_external_delete_claim(response):
    assert not _warns(response)
