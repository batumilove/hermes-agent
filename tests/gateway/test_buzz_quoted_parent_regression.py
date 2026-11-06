"""Exact-channel parent attachment tests; no credentials or external I/O."""
from unittest.mock import AsyncMock
import hashlib
import pytest
from tests.gateway.test_buzz_adapter import _make_adapter, _buzz_mod, _event, CHANNEL, OTHER_PUBKEY, CachedMedia

PAYLOAD = b'parent image'
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
URL = f'https://test.relay/media/{DIGEST}.png'


def parent_event(channel=CHANNEL, author=OTHER_PUBKEY):
    event = _event('parent', pubkey=author, content='')
    event['tags'] = [['h', channel], ['imeta', f'url {URL}', f'x {DIGEST}', f'size {len(PAYLOAD)}', 'm image/png', 'filename parent.png']]
    return event


def setup_adapter():
    adapter = _make_adapter()
    adapter._message_handler = AsyncMock()
    adapter.handle_message = AsyncMock()
    adapter.send_reaction = AsyncMock()
    adapter._resolve_user_name = AsyncMock(return_value='User')
    adapter._run_cli = AsyncMock(side_effect=AssertionError('no CLI'))
    adapter._download_attachment = AsyncMock(return_value=CachedMedia('/isolated/parent.png', 'image/png', 'image', 'parent.png'))
    return adapter, adapter._new_channel_state('dm')


def child():
    event = _event('child', content='describe quoted image')
    event['tags'].append(['e', 'parent', '', 'reply'])
    return event


@pytest.mark.asyncio
async def test_imeta_only_parent_is_delivered_from_normal_handler():
    adapter, state = setup_adapter()
    adapter._remember_event(state, parent_event())
    await adapter._handle_event(CHANNEL, state, child())
    delivered = adapter.handle_message.await_args.args[0]
    assert delivered.media_urls == ['/isolated/parent.png']
    assert delivered.reply_to_author_id == OTHER_PUBKEY
    adapter._download_attachment.assert_awaited_once()
    adapter._run_cli.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['foreign', 'unauthorized', 'revoked', 'child-denied', 'expired', 'absent', 'snippet-only', 'ambiguous', 'non-true'])
async def test_parent_authority_fail_closed(monkeypatch, boundary):
    adapter, state = setup_adapter()
    parent = parent_event(channel='foreign' if boundary == 'foreign' else CHANNEL, author='b' * 64)
    if boundary == 'ambiguous':
        parent['tags'].append(['h', 'foreign'])
    if boundary == 'unauthorized':
        adapter.set_authorization_check(lambda user, *_: user == OTHER_PUBKEY)
    if boundary == 'snippet-only':
        adapter._store_event_meta(state, 'parent', 'b' * 64, URL)
    elif boundary != 'absent':
        adapter._remember_event(state, parent)
    if boundary == 'revoked':
        adapter.set_authorization_check(lambda user, *_: user == OTHER_PUBKEY)
    if boundary == 'non-true':
        adapter.set_authorization_check(lambda *_: 1)
    if boundary == 'child-denied':
        adapter.set_authorization_check(lambda *_: False)
    if boundary == 'expired':
        monkeypatch.setattr(_buzz_mod.time, 'monotonic', lambda: 10**15)
    await adapter._handle_event(CHANNEL, state, child())
    adapter._download_attachment.assert_not_awaited()
    assert adapter.handle_message.await_args.args[0].media_urls == []
    adapter._run_cli.assert_not_awaited()


@pytest.mark.asyncio
async def test_quote_does_not_broaden_mention_policy():
    adapter, _ = setup_adapter()
    state = adapter._new_channel_state('group')
    adapter._remember_event(state, parent_event())
    await adapter._handle_event(CHANNEL, state, child())
    adapter.handle_message.assert_not_awaited()
    adapter._download_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_and_parent_share_budget_and_deduplicate(monkeypatch):
    adapter, state = setup_adapter()
    adapter._remember_event(state, parent_event())
    event = child()
    event['tags'].extend(parent_event()['tags'][1:])
    monkeypatch.setattr(_buzz_mod, '_MAX_INBOUND_ATTACHMENTS', 1)
    await adapter._handle_event(CHANNEL, state, event)
    adapter._download_attachment.assert_awaited_once()
    assert adapter.handle_message.await_args.args[0].media_urls == ['/isolated/parent.png']


@pytest.mark.asyncio
async def test_parent_uses_authenticated_integrity_checked_transport(monkeypatch):
    import httpx
    adapter, state = setup_adapter()
    del adapter._download_attachment
    adapter._private_key = '0' * 63 + '1'
    adapter._auth_tag = 'synthetic-owner'
    adapter._remember_event(state, parent_event())
    real = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['authorization'].startswith('Nostr ')
        assert request.headers['x-auth-tag'] == 'synthetic-owner'
        return httpx.Response(200, content=PAYLOAD)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    cache = AsyncMock(return_value=CachedMedia('/isolated/parent.png', 'image/png', 'image', 'parent.png'))
    monkeypatch.setattr(_buzz_mod, 'cache_media_bytes_async', cache)
    await adapter._handle_event(CHANNEL, state, child())
    assert len(calls) == 1
    cache.assert_awaited_once_with(PAYLOAD, filename='parent.png', mime_type='image/png')
    assert adapter.handle_message.await_args.args[0].media_urls == ['/isolated/parent.png']


@pytest.mark.asyncio
async def test_seeded_parent_metadata_is_available_without_seed_download():
    import json
    adapter, _ = setup_adapter()
    adapter._restore_channel_state = lambda *_: False
    adapter._run_cli = AsyncMock(return_value=(0, json.dumps([parent_event()]), ''))
    await adapter._seed_channel(CHANNEL, 'dm')
    adapter._download_attachment.assert_not_awaited()
    await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], child())
    adapter._download_attachment.assert_awaited_once()
    assert adapter.handle_message.await_args.args[0].media_urls == ['/isolated/parent.png']


@pytest.mark.asyncio
async def test_current_parent_combined_declared_byte_limit(monkeypatch):
    adapter, state = setup_adapter()
    adapter._remember_event(state, parent_event())
    event = child()
    event['tags'].append(['imeta', 'url https://test.relay/media/' + 'a' * 64 + '.png', 'x ' + 'a' * 64,
                          f'size {len(PAYLOAD)}', 'm image/png'])
    monkeypatch.setattr(_buzz_mod, '_MAX_INBOUND_ATTACHMENT_BYTES', len(PAYLOAD))
    await adapter._handle_event(CHANNEL, state, event)
    adapter._download_attachment.assert_awaited_once()
    assert adapter._download_attachment.await_args.args[0]['sha256'] == 'a' * 64


def test_parent_cache_is_bounded_and_does_not_retain_oversized_urls(monkeypatch):
    adapter, state = setup_adapter()
    monkeypatch.setattr(adapter, '_THREAD_ROOT_CACHE', 2)
    for n in range(4):
        event = parent_event()
        event['id'] = str(n)
        adapter._remember_event(state, event)
    assert list(state['quoted_attachments']) == ['2', '3']
    event = parent_event()
    event['tags'][1][1] = 'url https://test.relay/' + 'x' * 5000
    adapter._remember_event(state, event)
    assert 'parent' not in state['quoted_attachments']
