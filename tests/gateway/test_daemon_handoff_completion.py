"""A native delivery handoff must not escape into replay frames or stall the FIFO."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway.post_response import FinalResponseHandoff


def handoff():
    return FinalResponseHandoff("reply", runner=SimpleNamespace(lock=threading.Lock()),
        session_key="route", session_lineage="s", expected_session_id="s", new_session_id=None,
        active_turn_token="owned", last_prompt_tokens=0, touch_activity=True,
        append_final_text=False, clear_resume_pending=True)


def test_completion_projects_handoff_to_plain_wire_text():
    from copy import deepcopy
    from gateway.session_settlement_recovery import completion_payload
    response = handoff()
    payload = completion_payload({'admission_id': 'a'}, {'outcome': 'completed'}, response, None)
    assert type(payload['text']) is str
    assert type(payload['content']) is str
    assert deepcopy(payload)['text'] == 'reply'
    assert response.active_turn_token == 'owned'


@pytest.mark.asyncio
@pytest.mark.parametrize('settled', [False, True])
async def test_only_settled_handoff_releases_fifo_marker_wait(settled):
    from gateway.platforms.base import BasePlatformAdapter
    response = handoff()
    response.obligation_id = "durable-receipt"
    response.delivery_settled = settled
    released = asyncio.Event()
    event = SimpleNamespace(_gateway_post_response_handoff=response,
                            _gateway_marker_released=released, _gateway_active_turn_token='owned')
    await BasePlatformAdapter._release_turn_marker(SimpleNamespace(), event)
    assert released.is_set() == settled
    assert event._gateway_active_turn_token == 'owned'
