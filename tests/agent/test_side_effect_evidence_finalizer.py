"""Turn-finalizer integration tests for side-effect evidence regulation."""

from __future__ import annotations

from tests.agent.test_turn_finalizer_final_response_persistence import FakeAgent
from agent.turn_finalizer import finalize_turn


def _run_finalizer(agent, messages, final_response):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="text_response(2)",
    )


# ── Integration: finalizer appends footer to delivered response ─────────


def test_finalizer_appends_side_effect_warning_when_claim_lacks_current_turn_evidence(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    # Messages end with a tool result so the finalizer appends final_response.
    messages = [
        {"role": "user", "content": "create an issue"},
        {
            "role": "assistant",
            "content": "I will do it.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": "{}"},
    ]

    result = _run_finalizer(agent, messages, "I created the GitHub issue successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "github" in result["final_response"]
    # Footer must NOT leak into persisted messages (cache safety).
    assert result["messages"][-1]["role"] == "assistant"
    assert "Side-effect evidence regulator" not in result["messages"][-1]["content"]
    # Persisted messages got the clean final_response (without footer).
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == "I created the GitHub issue successfully."


def test_finalizer_does_not_warn_when_current_turn_tool_evidence_exists(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "send it"},
        {
            "role": "assistant",
            "content": "I will send it.",
            "tool_calls": [{"id": "call-1", "function": {"name": "send_message", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "send_message", "content": '{"message_id":123,"status":"sent"}'},
    ]

    result = _run_finalizer(agent, messages, "I sent the message successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_ignores_prior_turn_side_effect_evidence(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "old"},
        {"role": "tool", "name": "send_message", "content": '{"message_id":1}'},
        {"role": "assistant", "content": "Sent."},
        {"role": "user", "content": "new"},
    ]

    result = _run_finalizer(agent, messages, "I sent the message successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "send_message" in result["final_response"]


# ── False-positive: words that look like claims but aren't ──────────────


def test_finalizer_no_warning_for_cron_config_discussion(monkeypatch):
    """Mentioning 'cron' in 'cron config' or 'cron schedule' context without
    a success claim should not trigger the regulator."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "what is cron?"},
    ]

    result = _run_finalizer(agent, messages, "The cron config file is at /etc/crontab.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_generic_discussion_without_action_claims(monkeypatch):
    """Words like 'upload', 'deploy', 'delete' in non-claim context should
    not trigger the regulator."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "explain deployment"},
    ]

    result = _run_finalizer(
        agent, messages, "Deployment is the process of moving code to production."
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_negated_side_effect_claims(monkeypatch):
    """Explicit denials are not success claims and must not demand evidence."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify the prior report"}]

    result = _run_finalizer(
        agent,
        messages,
        (
            "I have not sent the message. "
            "Nothing was deployed, and nothing was deleted."
        ),
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_inspected_historical_state(monkeypatch):
    """Describing inspected code/history is not a fresh side-effect claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "what is the current state?"}]

    result = _run_finalizer(
        agent,
        messages,
        (
            "The structural fix was deployed earlier. "
            "The automatic optimizer is removed from the current code path."
        ),
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_when_response_has_no_side_effect_claims(monkeypatch):
    """A normal informational response should never get a footer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "what is 2+2?"},
    ]

    result = _run_finalizer(agent, messages, "The answer is 4.")

    assert "Side-effect evidence regulator" not in result["final_response"]


# ── Write-file / terminal evidence integration ──────────────────────────


def test_terminal_positive_result_satisfies_generic_side_effect_claim(monkeypatch):
    """A terminal tool result with exit_code/status should satisfy deploy/delete
    claims via the generic_side_effect evidence bucket."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": '{"exit_code":0,"output":"Deployed."}'},
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_terminal_error_result_does_not_satisfy_side_effect_claim(monkeypatch):
    """A terminal result that looks like an error should NOT satisfy the claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "deploy it"},
        {
            "role": "assistant",
            "content": "Deploying.",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": '{"error":"connection refused","exit_code":1}'},
    ]

    result = _run_finalizer(agent, messages, "I deployed the service successfully.")

    assert "Side-effect evidence regulator" in result["final_response"]


def test_finalizer_still_warns_for_affirmative_claim_with_no_errors_phrase(monkeypatch):
    """The word 'no' after the action verb must not negate a success claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy it"}]

    result = _run_finalizer(agent, messages, "I deployed the service with no errors.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "deploy" in result["final_response"]


def test_finalizer_warns_for_first_person_claim_about_earlier_version(monkeypatch):
    """An object described as earlier is still a first-person completion claim."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy it"}]

    result = _run_finalizer(agent, messages, "I deployed the earlier version.")

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "deploy" in result["final_response"]


def test_finalizer_no_warning_for_negated_or_historical_other_categories(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify prior claims"}]
    cases = (
        "The GitHub issue was not created.",
        "The cron job was paused earlier.",
        "The file was uploaded previously.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_warns_for_positive_action_after_negative_contrast(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "deploy the new service"}]
    cases = (
        "I have not deployed the old service, but I deployed the new service successfully.",
        "Previously the deploy failed, but I deployed the service successfully.",
        "I did not deploy the old service and then I deployed the new service successfully.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" in result["final_response"], response
        assert "deploy" in result["final_response"]


def test_finalizer_no_warning_for_genuinely_historical_first_person_action(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect history"}]
    cases = (
        "I deployed it earlier.",
        "Earlier, I sent the message.",
        "I previously uploaded the file.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_no_warning_for_contracted_negation(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "verify prior claims"}]
    cases = (
        "I haven't sent the message.",
        "I couldn't deploy the service.",
        "I don't believe I sent the message.",
        "I wouldn't delete the record.",
        "I can't upload the file.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_does_not_treat_generic_job_as_cron_context(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "run build"}]

    result = _run_finalizer(agent, messages, "I ran the build job successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_preserves_category_context_across_sentences(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "do it"}]
    cases = (
        ("The message is ready. I sent it successfully.", "send_message"),
        ("The GitHub issue is #123. I created it successfully.", "github"),
    )

    for response, category in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" in result["final_response"], response
        assert category in result["final_response"]


def test_finalizer_warns_for_positive_action_after_plain_and(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "do it"}]

    result = _run_finalizer(
        agent,
        messages,
        "I did not create the issue and submitted the pull request successfully.",
    )

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "github" in result["final_response"]


def test_finalizer_no_warning_for_first_person_inspection_report(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect it"}]

    result = _run_finalizer(
        agent,
        messages,
        "I inspected the logs and confirmed the message was sent.",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_warns_for_current_action_after_historical_action(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "upload new file"}]

    result = _run_finalizer(
        agent,
        messages,
        "I previously uploaded the old file and uploaded the new file successfully.",
    )

    assert "Side-effect evidence regulator" in result["final_response"]
    assert "upload" in result["final_response"]


def test_finalizer_warns_for_unqualified_passive_completion_claims(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "do it"}]
    cases = (
        ("The message was sent.", "send_message"),
        ("The service was deployed.", "deploy"),
        ("The record was deleted.", "delete"),
    )

    for response, category in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" in result["final_response"], response
        assert category in result["final_response"]


def test_finalizer_no_warning_for_neither_nor(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "check issue"}]

    result = _run_finalizer(
        agent,
        messages,
        "I neither created nor submitted the GitHub issue.",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_calendar_history(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect history"}]
    cases = (
        "The service was deployed yesterday.",
        "The service was deployed last week.",
        "The service was deployed earlier today.",
        "Last week, I deployed it.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_no_warning_for_present_state_description(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "inspect state"}]
    cases = (
        "The cron job is paused.",
        "The issue is currently opened.",
    )

    for response in cases:
        result = _run_finalizer(agent, messages, response)
        assert "Side-effect evidence regulator" not in result["final_response"], response


def test_finalizer_does_not_borrow_category_context_from_later_sentence(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "work locally"}]

    result = _run_finalizer(
        agent,
        messages,
        "I created a local file successfully. GitHub access is unavailable.",
    )

    assert "Side-effect evidence regulator" not in result["final_response"]


# ── Edge cases ──────────────────────────────────────────────────────────


def test_finalizer_no_warning_when_verifier_disabled(monkeypatch):
    """When the config toggle is off, no footer should be appended."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    # Flip the toggle off by monkeypatching the method.
    monkeypatch.setattr(
        agent, "_side_effect_evidence_verifier_enabled", lambda: False
    )
    messages = [
        {"role": "user", "content": "create an issue"},
    ]

    result = _run_finalizer(agent, messages, "I created the GitHub issue successfully.")

    assert "Side-effect evidence regulator" not in result["final_response"]


def test_finalizer_no_warning_for_empty_final_response(monkeypatch):
    """Empty final_response should not trigger the footer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "hi"}]

    result = _run_finalizer(agent, messages, "")

    assert "Side-effect evidence regulator" not in (result.get("final_response") or "")


def test_finalizer_no_warning_when_interrupted(monkeypatch):
    """Interrupted turns should not get the footer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [{"role": "user", "content": "create an issue"}]

    result = finalize_turn(
        agent,
        final_response="I created the GitHub issue successfully.",
        api_call_count=2,
        interrupted=True,  # interrupted
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="interrupted",
    )

    assert "Side-effect evidence regulator" not in (result.get("final_response") or "")


def test_footer_empty_when_no_messages():
    """build_side_effect_evidence_footer should handle empty messages."""
    from agent.harness_learning import build_side_effect_evidence_footer

    assert build_side_effect_evidence_footer([], "I sent the message.") != ""
    # None messages should be treated as empty, not crash.
    assert build_side_effect_evidence_footer(messages=None or [], final_response="I sent the message.") != ""
