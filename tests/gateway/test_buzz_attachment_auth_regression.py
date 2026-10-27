"""Private imeta transport regressions; all HTTP stays in MockTransport."""
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.gateway.test_buzz_adapter import _make_adapter, _buzz_mod

@pytest.mark.parametrize('value', ['https://test.relay:0', 'wss://test.relay:0', 'test.relay:0'])
def test_configured_zero_port_is_not_an_origin(value):
    assert _buzz_mod._attachment_origin(value) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('relay,host', [('https://test.relay', 'test.relay:0'), ('https://test.relay:0', 'test.relay')])
async def test_zero_port_refused_before_signing_and_client(monkeypatch, relay, host):
    adapter = _make_adapter()
    adapter.relay_url = relay
    adapter._attachment_origins = {o for o in [_buzz_mod._attachment_origin(relay)] if o is not None}
    monkeypatch.setattr(_buzz_mod._nostr_auth, 'build_media_get_authorization', lambda **kw: pytest.fail('signed zero-port authority'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: pytest.fail('constructed client for zero-port authority'))
    adapter._private_key = '0' * 63 + '1'
    assert await adapter._download_attachment(metadata(url=f'https://{host}/media/{DIGEST}.bin')) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('authority', ['test.relay', 'test.relay:443', 'test.relay:8443'])
async def test_authority_ports_preserved(monkeypatch, authority):
    adapter = _make_adapter()
    adapter.relay_url = f'https://{authority}'
    adapter._attachment_origins = {_buzz_mod._attachment_origin(adapter.relay_url)}
    adapter._private_key = '0' * 63 + '1'
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == httpx.URL(f'https://{authority}/media/{DIGEST}.bin')
        raw = request.headers['authorization'].removeprefix('Nostr ')
        event = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
        assert ['server', authority] in event['tags']
        return httpx.Response(200, content=PAYLOAD)
    transport(monkeypatch, handler)
    monkeypatch.setattr(_buzz_mod, 'cache_media_bytes_async', AsyncMock(return_value='cached'))
    assert await adapter._download_attachment(metadata(url=f'https://{authority}/media/{DIGEST}.bin')) == 'cached'
    assert len(calls) == 1


PAYLOAD = b'private relay attachment'
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def metadata(**overrides):
    return {'url': f'https://test.relay/media/{DIGEST}.bin', 'sha256': DIGEST,
            'size': len(PAYLOAD), 'filename': 'file.bin',
            'mime_type': 'application/octet-stream', **overrides}


def verify_bip340(event):
    """Independent test-only BIP340 equation; no production signing helpers."""
    p = 2**256 - 2**32 - 977
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    g = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
         0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)
    def add(a, b):
        if a is None: return b
        if b is None: return a
        x, y = a
        u, v = b
        if x == u and (y != v or y == 0): return None
        slope = ((3*x*x) * pow(2*y, -1, p) if a == b else (v-y)*pow(u-x, -1, p)) % p
        z = (slope*slope-x-u) % p
        return z, (slope*(x-z)-y) % p
    def mul(k, a):
        out = None
        while k:
            if k & 1: out = add(out, a)
            a = add(a, a)
            k >>= 1
        return out
    x = int(event['pubkey'], 16)
    assert x < p
    y = pow((x*x*x+7) % p, (p+1)//4, p)
    assert y*y % p == (x*x*x+7) % p
    key = (x, y if y % 2 == 0 else p-y)
    sig = bytes.fromhex(event['sig'])
    r, s = int.from_bytes(sig[:32]), int.from_bytes(sig[32:])
    assert r < p and s < n
    tag = hashlib.sha256(b'BIP0340/challenge').digest()
    e = int.from_bytes(hashlib.sha256(tag+tag+sig[:32]+bytes.fromhex(event['pubkey'])+bytes.fromhex(event['id'])).digest()) % n
    result = add(mul(s, g), mul(n-e, key))
    assert result is not None and result[1] % 2 == 0 and result[0] == r


def transport(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.asyncio
async def test_private_imeta_uses_blossom_auth_and_owner_header(monkeypatch):
    adapter = _make_adapter()
    adapter._private_key = '0' * 63 + '1'  # synthetic test identity, never ambient credentials
    adapter._auth_tag = '["auth","owner","conditions","signature"]'
    def handler(request):
        assert request.headers['x-auth-tag'] == adapter._auth_tag
        header = request.headers['authorization']
        assert header.startswith('Nostr ')
        raw = header.removeprefix('Nostr ')
        event = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
        assert event['kind'] == 24242
        assert event['content'] == 'Get media'
        assert ['t', 'get'] in event['tags']
        assert ['server', 'test.relay'] in event['tags']
        assert ['expiration', str(event['created_at'] + 60)] in event['tags']
        assert len(event['sig']) == 128
        verify_bip340(event)
        canonical = json.dumps([0, event['pubkey'], event['created_at'], event['kind'], event['tags'], event['content']], separators=(',', ':'), ensure_ascii=False).encode()
        assert hashlib.sha256(canonical).hexdigest() == event['id']
        return httpx.Response(200, content=PAYLOAD)
    transport(monkeypatch, handler)
    cache = AsyncMock(return_value='verified-cache')
    monkeypatch.setattr(_buzz_mod, 'cache_media_bytes_async', cache)
    assert await adapter._download_attachment(metadata()) == 'verified-cache'
    cache.assert_awaited_once_with(PAYLOAD, filename='file.bin', mime_type='application/octet-stream')


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['size', 'hash', 'redirect', 'oversize', 'length', 'encoding'])
async def test_authenticated_transfer_rejects_invalid_data(monkeypatch, damage):
    adapter = _make_adapter()
    adapter._private_key = '0' * 63 + '1'
    calls = []
    def handler(request):
        calls.append(request)
        assert len(calls) == 1
        assert str(request.url) == metadata()['url']
        assert 'authorization' in request.headers
        return httpx.Response(302 if damage == 'redirect' else 200,
                              content=(b'x' * len(PAYLOAD) if damage == 'hash' else PAYLOAD) + (b'x' if damage == 'oversize' else b''),
                              headers={'location': 'https://evil.invalid/media/x',
                                       **({'content-length': '999'} if damage == 'length' else {}),
                                       **({'content-encoding': 'gzip'} if damage == 'encoding' else {})})
    transport(monkeypatch, handler)
    cache = AsyncMock()
    monkeypatch.setattr(_buzz_mod, 'cache_media_bytes_async', cache)
    item = metadata(**({'size': len(PAYLOAD) + 1} if damage == 'size' else {}))
    assert await adapter._download_attachment(item) is None
    cache.assert_not_awaited()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_digest_conflict_rejected_before_signing_or_network(monkeypatch):
    adapter = _make_adapter()
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: pytest.fail('network before digest validation'))
    assert await adapter._download_attachment(metadata(sha256='a' * 64)) is None


@pytest.mark.asyncio
async def test_optional_third_party_host_never_receives_credentials(monkeypatch):
    adapter = _make_adapter(extra={'attachment_hosts': ['cdn.test']})
    adapter._private_key = '0' * 63 + '1'
    adapter._auth_tag = '["auth","owner","conditions","signature"]'
    def handler(request):
        assert 'authorization' not in request.headers
        assert 'x-auth-tag' not in request.headers
        return httpx.Response(200, content=PAYLOAD)
    transport(monkeypatch, handler)
    cache = AsyncMock(return_value='third-party-cache')
    monkeypatch.setattr(_buzz_mod, 'cache_media_bytes_async', cache)
    assert await adapter._download_attachment(metadata(url=f'https://cdn.test/media/{DIGEST}.bin')) == 'third-party-cache'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 401])
async def test_handler_private_imeta_native_delivery_and_failure_note(monkeypatch, status):
    from tests.gateway.test_buzz_adapter import CachedMedia, OTHER_PUBKEY, CHANNEL, _event
    adapter = _make_adapter()
    adapter._private_key = '0' * 63 + '1'
    adapter._user_names[OTHER_PUBKEY] = 'Other'
    adapter._message_handler = AsyncMock()
    adapter.handle_message = AsyncMock()
    adapter.send_reaction = AsyncMock()
    adapter._run_cli = AsyncMock(side_effect=AssertionError('no CLI download'))
    state = adapter._new_channel_state('dm')
    def handler(request):
        assert 'authorization' in request.headers
        return httpx.Response(status, content=PAYLOAD)
    transport(monkeypatch, handler)
    cached = CachedMedia('/isolated/file.bin', 'application/octet-stream', 'document', 'file.bin')
    cache = AsyncMock(return_value=cached)
    monkeypatch.setattr(_buzz_mod, 'cache_media_bytes_async', cache)
    event = _event('private-imeta', content='inspect')
    event['tags'].append(['imeta', f'url https://test.relay/media/{DIGEST}.bin',
                          f'x {DIGEST}', f'size {len(PAYLOAD)}', 'filename file.bin'])
    await adapter._handle_event(CHANNEL, state, event)
    delivered = adapter.handle_message.await_args.args[0]
    assert delivered.media_urls == ([cached.path] if status == 200 else [])
    assert ('could not be downloaded' in delivered.text) is (status != 200)
    adapter._run_cli.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticated_stream_deadline_closes_client(monkeypatch):
    import asyncio
    adapter = _make_adapter()
    adapter._private_key = '0' * 63 + '1'
    closed = []
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            yield PAYLOAD
        async def aclose(self):
            closed.append(True)
    transport(monkeypatch, lambda request: httpx.Response(200, stream=SlowStream()))
    monkeypatch.setattr(_buzz_mod, '_ATTACHMENT_DOWNLOAD_TIMEOUT', .05)
    assert await adapter._download_attachment(metadata()) is None
    assert closed == [True]
