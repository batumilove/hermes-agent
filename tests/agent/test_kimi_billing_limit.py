"""Kimi billing-cycle refusal classification."""
from tests.agent.test_error_classifier import MockAPIError
from agent.error_classifier import classify_api_error, FailoverReason

def test_kimi_403_billing_cycle_usage_limit_classified_as_billing():
    e = MockAPIError(
        "Error code: 403",
        status_code=403,
        body={
            "error": {
                "message": (
                    "You've reached your usage limit for this billing cycle. "
                    "Your quota will be refreshed in the next cycle."
                )
            }
        },
    )

    result = classify_api_error(e, provider="kimi-coding")

    assert result.reason == FailoverReason.billing
    assert result.retryable is False
    assert result.should_rotate_credential is True
    assert result.should_fallback is True
