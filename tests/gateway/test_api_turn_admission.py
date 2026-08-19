"""Agent-serving-endpoint turn admission via ``_admit_api_agent_request``.

P0-A part 2 wiring: every agent-serving API endpoint goes through the
``_admit_api_agent_request`` decorator, so bounding write-heavy runs at that
ONE chokepoint bounds them across all of them. Pinned here:

* queue-full answers 429 with a Retry-After header before any agent work
  starts (the handler itself must never run);
* the turn slot is released when the handler completes (next request on the
  same profile admits normally);
* admission fails open when the gateway home cannot be resolved (an
  observability bound must never take the endpoint down).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway import write_admission as gwa


def _make_adapter(api_key: str = "sk-test") -> APIServerAdapter:
    config = PlatformConfig(enabled=True, extra={"key": api_key})
    return APIServerAdapter(config)


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def admission_profile(tmp_path, monkeypatch):
    """A profile home whose admission controller is fresh and tightly bound."""
    home = tmp_path / "profile-home"
    home.mkdir()
    gwa.reset_gateway_admissions_for_tests()
    adm = gwa.get_admission_for_profile(str(home))
    adm._override_for_test(capacity=1, queue_limit=0)
    monkeypatch.setattr(
        "gateway.platforms.api_server.get_hermes_home",
        lambda: str(home),
        raising=False,
    )
    # get_hermes_home is imported inside the decorator body via
    # hermes_constants; patch it there too.
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: str(home))
    yield str(home)
    adm._override_for_test(
        capacity=gwa._TURN_ADMISSION_CAPACITY,
        queue_limit=gwa._TURN_ADMISSION_QUEUE_LIMIT,
    )
    gwa.reset_gateway_admissions_for_tests()


async def _make_client(adapter, handler):
    app = web.Application()
    app.router.add_post("/v1/probe", handler)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


class TestTurnAdmissionAtDecorator:
    @pytest.mark.asyncio
    async def test_queue_full_answers_429_before_handler_runs(
        self, adapter, admission_profile
    ):
        adm = gwa.get_admission_for_profile(admission_profile)
        holder = adm.acquire(session_key="hold")  # exhaust capacity=1

        ran = []

        async def handler(self, request):
            ran.append(True)
            return web.json_response({"ok": True})

        from gateway.platforms.api_server import _admit_api_agent_request

        wrapped = _admit_api_agent_request(handler)
        app = web.Application()
        app.router.add_post("/v1/probe", wrapped.__get__(adapter))
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post(
                "/v1/probe", headers={"Authorization": "Bearer sk-test"}
            )
            assert resp.status == 429
            assert resp.headers.get("Retry-After") is not None
            body = await resp.json()
            assert body["error"]["type"] == "rate_limit_error"
            assert body["error"]["code"] == "state_db_write_admission_full"
            assert ran == []  # handler never ran
        finally:
            await client.close()
            holder.release()

    @pytest.mark.asyncio
    async def test_slot_released_after_handler_completes(
        self, adapter, admission_profile
    ):
        adm = gwa.get_admission_for_profile(admission_profile)

        async def handler(self, request):
            stats = adm.stats()
            assert stats["in_flight"] >= 1  # our slot is held during the turn
            return web.json_response({"ok": True})

        from gateway.platforms.api_server import _admit_api_agent_request

        wrapped = _admit_api_agent_request(handler)
        app = web.Application()
        app.router.add_post("/v1/probe", wrapped.__get__(adapter))
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            for _ in range(3):
                resp = await client.post(
                    "/v1/probe", headers={"Authorization": "Bearer sk-test"}
                )
                assert resp.status == 200
                assert adm.stats()["in_flight"] == 0  # released after each
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_admission_fails_open_without_home(self, adapter):
        async def handler(self, request):
            return web.json_response({"ok": True})

        from gateway.platforms.api_server import _admit_api_agent_request

        import hermes_constants

        orig = hermes_constants.get_hermes_home
        hermes_constants.get_hermes_home = lambda: None
        try:
            wrapped = _admit_api_agent_request(handler)
            app = web.Application()
            app.router.add_post("/v1/probe", wrapped.__get__(adapter))
            server = TestServer(app)
            client = TestClient(server)
            await client.start_server()
            try:
                resp = await client.post(
                    "/v1/probe", headers={"Authorization": "Bearer sk-test"}
                )
                assert resp.status == 200
            finally:
                await client.close()
        finally:
            hermes_constants.get_hermes_home = orig
