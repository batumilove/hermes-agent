"""Known GHSA-pmqc-57g8-c22c: authenticate before spending delivery resources."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from plugins.platforms.feishu import adapter as feishu



def _adapter(*, token="quota-token", key="quota-key"):
    return feishu.FeishuAdapter(PlatformConfig(extra={
        "app_id": "public-test-app", "app_secret": "public-test-secret", "connection_mode": "webhook",
        "verification_token": token, "encrypt_key": key,
    }))


def _signed_headers(body, key="quota-key"):
    prefix = "1700000000audit-nonce" + key
    return {"Content-Type": "application/json", "x-lark-request-timestamp": "1700000000",
            "x-lark-request-nonce": "audit-nonce",
            "x-lark-signature": hashlib.sha256(prefix.encode() + body).hexdigest()}


@asynccontextmanager
async def _client(adapter):
    app = web.Application(client_max_size=feishu._FEISHU_WEBHOOK_MAX_BODY_BYTES)
    app.router.add_post("/feishu/webhook", adapter._handle_webhook_request)
    async with TestClient(TestServer(app)) as client:
        yield client


def test_invalid_webhook_traffic_preserves_authenticated_delivery_quota():
    async def scenario():
        adapter = _adapter()
        dispatched = Mock(wraps=adapter._on_message_read_event)
        adapter._on_message_read_event = dispatched
        valid = json.dumps({"header": {"token": "quota-token", "event_type": "im.message.message_read_v1"}}).encode()
        async with _client(adapter) as client:
            wrong_token = valid.replace(b"quota-token", b"wrong-token")
            for index in range(feishu._FEISHU_WEBHOOK_RATE_LIMIT_MAX):
                body = wrong_token if index % 2 else valid
                headers = _signed_headers(body) if index % 2 else {"Content-Type": "application/json"}
                response = await client.post("/feishu/webhook", data=body, headers=headers)
                assert response.status == 401
                await response.read()
            response = await client.post("/feishu/webhook", data=valid, headers=_signed_headers(valid))
            assert response.status == 200
            assert (await response.json())["code"] == 0
            dispatched.assert_called_once()
            assert next(iter(adapter._webhook_rate_counts.values()))[0] == 1
            for _ in range(feishu._FEISHU_WEBHOOK_RATE_LIMIT_MAX - 1):
                response = await client.post("/feishu/webhook", data=valid, headers=_signed_headers(valid))
                assert response.status == 200
                await response.read()
            response = await client.post("/feishu/webhook", data=valid, headers=_signed_headers(valid))
            assert response.status == 429
            await response.read()
            assert dispatched.call_count == 120
    asyncio.run(scenario())


def test_webhook_challenges_and_rejections_keep_authentication_state_bounded():
    async def scenario():
        adapter = _adapter(token="", key="quota-key")
        challenge = {"type": "url_verification", "challenge": "public-test-challenge"}
        async with _client(adapter) as client:
            response = await client.post("/feishu/webhook", json=challenge)
            assert response.status == 401
            assert "public-test-challenge" not in await response.text()
            signed_event = b'{"header":{"event_type":"im.message.message_read_v1"}}'
            response = await client.post("/feishu/webhook", data=signed_event, headers=_signed_headers(signed_event))
            assert response.status == 200  # encrypt-key-only ordinary events remain usable
            await response.read()
            adapter._verification_token = "quota-token"
            challenge["token"] = "quota-token"
            response = await client.post("/feishu/webhook", json=challenge)
            assert response.status == 200
            assert await response.json() == {"challenge": "public-test-challenge"}
            for payload in ([1], {"header": []}, {"header": "invalid"}):
                response = await client.post("/feishu/webhook", json=payload)
                assert response.status == 400
                await response.read()
            body = b'{"header":{"token":"quota-token"},"event":' + b'[' * 70 + b'0' + b']' * 70 + b'}'
            response = await client.post("/feishu/webhook", data=body, headers=_signed_headers(body))
            assert response.status == 400
            await response.read()
            for _ in range(600):
                response = await client.post("/feishu/webhook", data=b"{", headers={"Content-Type": "application/json"})
                await response.read()
                if response.status == 429:
                    break
                assert response.status == 400
            else:
                raise AssertionError("unauthenticated parsing was not bounded")
            assert next(iter(adapter._webhook_preauth_counts.values()))[0] == 600
        for index in range(4100):
            adapter._record_webhook_anomaly(f"public-test-source-{index}", "401")
        assert len(adapter._webhook_anomaly_counts) <= 4096
        assert next(iter(adapter._webhook_rate_counts.values()))[0] == 2
    asyncio.run(scenario())
