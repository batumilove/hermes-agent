"""Decision-table tests for ``_summarize_cron_failure_for_delivery``.

Covers the mis-classification bug where any error string containing
``timeout`` / ``timed out`` / ``readtimeout`` was rendered as a *provider*
timeout — even when the job was ``no_agent=True`` (a pure script job with no
LLM/provider involvement) and the timeout keyword lived inside a structured
script stdout blob rather than a provider error.

Decision table enforced here:

| # | job.no_agent | error shape                                  | expected classification       |
|---|--------------|----------------------------------------------|-------------------------------|
| 1 | True         | structured ``collector_timeout`` stdout      | script timeout (NOT provider) |
| 2 | True         | generic ``timed out`` text                   | script timeout (NOT provider) |
| 3 | False        | provider ``ReadTimeout`` error               | provider timeout (unchanged)  |
| 4 | True         | malformed / oversized stdout                 | bounded + sanitized script msg|
| 5 | False        | 429 / rate limit                             | rate limit (unchanged)        |
| 6 | False        | auth / 401                                   | auth error (unchanged)        |
| 7 | True         | 429 inside script stdout                     | script failure (NOT rate)     |
| 8 | True         | auth text inside script stdout               | script failure (NOT auth)     |

Cases 1, 2, 4, 7, 8 are the regression guards for the bug. Cases 3, 5, 6
protect the existing provider-path wording from collateral damage.
"""

from __future__ import annotations

import pytest

from cron.scheduler import _summarize_cron_failure_for_delivery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_agent_job(name: str = "collector") -> dict:
    return {"id": "job-1", "name": name, "no_agent": True}


def _agent_job(name: str = "nightly-report") -> dict:
    return {"id": "job-2", "name": name, "no_agent": False}


# ---------------------------------------------------------------------------
# Case 1 — no_agent structured collector_timeout stdout
# ---------------------------------------------------------------------------

def test_no_agent_structured_collector_timeout_not_provider():
    """The original bug: ActiveGraph collector timed out at 180s.

    The error string is the raw script output::

        Script exited with code 1
        stdout:
        {"alert":"ActiveGraph cron evidence","kind":"collector_timeout","timeout_seconds":180}

    This MUST NOT claim provider/fallback involvement. It should preserve a
    useful structured ``kind`` (``collector_timeout``) and the timeout seconds.
    """
    error = (
        'Script exited with code 1\n'
        'stdout:\n'
        '{"alert":"ActiveGraph cron evidence","kind":"collector_timeout","timeout_seconds":180}'
    )
    msg = _summarize_cron_failure_for_delivery(_no_agent_job(), error)

    assert "provider" not in msg.lower(), msg
    assert "fallback" not in msg.lower(), msg
    # Preserve a safe, useful structured kind so the operator knows *what* timed out.
    assert "collector_timeout" in msg, msg
    assert "180" in msg, msg


# ---------------------------------------------------------------------------
# Case 2 — no_agent generic "timed out" text
# ---------------------------------------------------------------------------

def test_no_agent_generic_timed_out_text_not_provider():
    """A no_agent job whose error merely says ``timed out`` must not be
    mis-rendered as a provider timeout."""
    error = "Script exited with code 124\ntimed out after 300s"
    msg = _summarize_cron_failure_for_delivery(_no_agent_job("watchdog"), error)

    assert "provider" not in msg.lower(), msg
    assert "fallback" not in msg.lower(), msg
    assert "watchdog" in msg, msg
    assert "timed out" in msg.lower() or "timeout" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Case 3 — agent-backed provider timeout (UNCHANGED behavior)
# ---------------------------------------------------------------------------

def test_agent_provider_timeout_wording_preserved():
    """An agent-backed job whose provider raised ``ReadTimeout`` must still
    render the existing provider-timeout wording. This is the regression
    guard against over-fixing."""
    error = "ReadTimeout: HTTPSConnectionPool(host='api.openai.com'): Read timed out"
    msg = _summarize_cron_failure_for_delivery(_agent_job(), error)

    assert "provider timeout" in msg.lower(), msg
    assert "fallback" in msg.lower(), msg


def test_agent_generic_timeout_wording_preserved():
    """Agent job with a plain ``timed out`` provider error stays provider-side."""
    msg = _summarize_cron_failure_for_delivery(_agent_job(), "The request timed out.")

    assert "provider timeout" in msg.lower(), msg
    assert "fallback" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Case 4 — no_agent malformed / oversized stdout (bounded + sanitized)
# ---------------------------------------------------------------------------

def test_no_agent_malformed_stdout_is_bounded_and_sanitizezd():
    """Malformed (non-JSON) no_agent stdout must still be classified as a
    script failure, bounded in length, and never claim provider involvement."""
    error = "Script exited with code 1\nstdout:\n" + "GARBAGE" * 500
    msg = _summarize_cron_failure_for_delivery(_no_agent_job(), error)

    assert "provider" not in msg.lower(), msg
    assert "fallback" not in msg.lower(), msg
    assert "script" in msg.lower(), msg
    # Bounded: never dump the full multi-KB blob into the delivery channel.
    assert len(msg) <= 220, f"message not bounded: {len(msg)} chars"


def test_no_agent_empty_error_is_safe():
    """A no_agent job with no error text must not crash and must not claim
    provider involvement."""
    msg = _summarize_cron_failure_for_delivery(_no_agent_job(), None)
    assert "provider" not in msg.lower(), msg
    assert "fallback" not in msg.lower(), msg
    assert "script" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Case 5 — agent rate-limit (UNCHANGED)
# ---------------------------------------------------------------------------

def test_agent_rate_limit_wording_preserved():
    msg = _summarize_cron_failure_for_delivery(_agent_job(), "429 Too Many Requests")
    assert "rate limit" in msg.lower(), msg
    assert "fallback" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Case 6 — agent auth (UNCHANGED)
# ---------------------------------------------------------------------------

def test_agent_auth_wording_preserved():
    msg = _summarize_cron_failure_for_delivery(_agent_job(), "401 Unauthorized")
    assert "authentication" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Case 7 — no_agent stdout containing "429" must NOT be rate-limit
# ---------------------------------------------------------------------------

def test_no_agent_stdout_with_429_not_rate_limit():
    """A script that happens to print ``429`` must not trip the provider
    rate-limit classifier for a no_agent job."""
    error = 'Script exited with code 1\nstdout:\n{"status":429,"retry":"later"}'
    msg = _summarize_cron_failure_for_delivery(_no_agent_job(), error)

    assert "rate limit" not in msg.lower(), msg
    assert "fallback" not in msg.lower(), msg
    assert "script" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Case 8 — no_agent stdout containing auth text must NOT be auth error
# ---------------------------------------------------------------------------

def test_no_agent_stdout_with_auth_text_not_auth_error():
    """A script whose stdout mentions ``authentication`` must not be
    mis-rendered as a provider auth error for a no_agent job."""
    error = 'Script exited with code 1\nstdout:\n{"msg":"authentication service reachable"}'
    msg = _summarize_cron_failure_for_delivery(_no_agent_job(), error)

    assert "authentication error" not in msg.lower(), msg
    assert "provider" not in msg.lower(), msg
    assert "script" in msg.lower(), msg
