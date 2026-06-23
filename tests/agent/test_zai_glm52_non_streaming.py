"""GLM-5.2 should use the regular non-streaming API path.

Z.AI's GLM-5.2 SSE endpoint can return business error 1305 (rate limit /
overload) while the regular chat completion endpoint succeeds.  Hermes should
skip streaming for this model instead of burning the first retry attempt on a
known-bad transport.
"""

import inspect

from agent import conversation_loop


def test_zai_glm52_is_forced_to_non_streaming():
    source = inspect.getsource(conversation_loop.run_conversation)

    assert 'provider", None) == "zai"' in source
    assert '"glm-5.2" in str(getattr(agent, "model", "")).lower()' in source
    assert "Start this\n                    # model on the regular API directly" in source
