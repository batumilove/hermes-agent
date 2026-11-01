"""Failed Mattermost POSTs must retain their delivery semantics through the real retry path."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from gateway.config import PlatformConfig
from plugins.platforms.mattermost.adapter import MattermostAdapter, MAX_POST_LENGTH


def response(status, body):
    result = MagicMock()
    result.status = status
    result.headers = {}
    result.text = AsyncMock(return_value=json.dumps(body))
    result.json = AsyncMock(return_value=body)
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result


def adapter(*responses):
    result = MattermostAdapter(PlatformConfig(enabled=True, token='fake', extra={'url':'https://mm.invalid'}))
    result._session = MagicMock()
    result._session.post.side_effect = responses
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [
    response(500, {'id':'app.post.save.app_error'}),
    response(401, {'id':'api.context.session_expired.app_error'}),
    response(403, {'id':'api.context.permissions.app_error'}),
    response(400, {'id':'api.context.invalid_param.app_error', 'message':'invalid root_id'}),
    TimeoutError(), aiohttp.ClientConnectionError('connection lost'),
])
async def test_failed_posts_never_become_a_formatting_resend(failure):
    client = adapter(failure, response(201, {'id':'unexpected-duplicate'}))
    result = await client._send_with_retry('channel', 'hello', base_delay=0)
    assert not result.success
    assert client._session.post.call_count == 1
    assert result.raw_response['mattermost_failure'] is True
    expected_uncertain = isinstance(failure, BaseException) or failure.status >= 500
    assert result.raw_response['delivery_uncertain'] is expected_uncertain


@pytest.mark.asyncio
async def test_partial_split_is_not_replayed_after_rate_limit():
    client = adapter(response(201, {'id':'visible-head'}), response(429, {'id':'api.context.rate_limit.app_error'}),
                     response(201, {'id':'duplicate-head'}))
    result = await client._send_with_retry('channel', 'x'*(MAX_POST_LENGTH+10), base_delay=0)
    assert not result.success
    assert client._session.post.call_count == 2
    assert result.continuation_message_ids == ('visible-head',)


@pytest.mark.asyncio
async def test_rate_limit_is_typed_and_retains_server_delay():
    reply = response(429, {'id':'api.context.rate_limit.app_error'})
    reply.headers = {'Retry-After':'120'}
    client = adapter(reply)
    result = await client._send_with_retry('channel', 'hello')
    assert not result.success
    assert result.error_kind == 'rate_limited'
    assert result.retry_after == 120
    assert client._session.post.call_count == 1


@pytest.mark.asyncio
async def test_typing_request_cannot_overwrite_invalid_root_receipt():
    refusal = response(400, {'id':'api.context.invalid_param.app_error', 'message':'invalid root_id'})
    client = adapter(refusal, response(200, {}), response(201, {'id':'flat-final'}))
    client._reply_mode = 'thread'
    client._api_get = AsyncMock(return_value={'id':'broken-root'})
    async def read_body():
        await client.send_typing('channel')
        return json.dumps({'id':'api.context.invalid_param.app_error', 'message':'invalid root_id'})
    refusal.text.side_effect = read_body
    result = await client._send_with_retry('channel', 'answer', reply_to='broken-root', metadata={'notify':True})
    assert result.success
    assert result.message_id == 'flat-final'
    assert client._session.post.call_count == 3
    final = client._session.post.call_args.kwargs['json']
    assert 'root_id' not in final
    assert 'Response formatting failed' not in final['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('replies, expected', [
    ([response(500, {'id':'app.post.save.app_error'})], 'ambiguous'),
    ([TimeoutError()], 'ambiguous'),
    ([aiohttp.ClientConnectionError('connection lost')], 'ambiguous'),
    ([response(403, {'id':'api.context.permissions.app_error'})], 'failed'),
    ([response(201, {'id':'visible-head'}), response(429, {'id':'rate_limit'})], 'ambiguous'),
])
async def test_stream_final_preserves_delivery_uncertainty(replies, expected):
    from gateway.stream_consumer import GatewayStreamConsumer
    client = adapter(*replies)
    consumer = GatewayStreamConsumer(client, 'channel')
    text = 'x'*(MAX_POST_LENGTH+10) if len(replies) == 2 else 'answer'
    assert await consumer._send_empty_fallback_final(text) == expected
    assert client._session.post.call_count == len(replies)
    assert not consumer._final_content_delivered


@pytest.mark.asyncio
async def test_stream_final_honors_long_rate_limit_without_another_post():
    from gateway.stream_consumer import GatewayStreamConsumer
    reply = response(429, {'id':'api.context.rate_limit.app_error'})
    reply.headers = {'Retry-After':'120'}
    client = adapter(reply)
    consumer = GatewayStreamConsumer(client, 'channel')
    assert await consumer._send_empty_fallback_final('answer') == 'preview'
    assert client._session.post.call_count == 1
    assert not consumer._final_content_delivered
