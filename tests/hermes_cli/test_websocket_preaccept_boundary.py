"""CVE-2026-53869: exercise each real route's pre-accept boundary.

No PTY or gateway is started: hostile headers must be rejected before that work.
The import-light helper controls separately prove valid loopback admission.
"""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('route', ['/api/pty', '/api/ws', '/api/pub', '/api/events'])
@pytest.mark.parametrize('headers', [
    {'host': 'evil.example', 'origin': 'http://evil.example'},
    {'host': 'localhost:9119', 'origin': 'https://evil.example'},
])
def test_all_affected_routes_reject_before_accept(route, headers, monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import hermes_cli.web_server as server

    monkeypatch.setattr(server.app.state, 'bound_host', '127.0.0.1', raising=False)
    monkeypatch.setattr(server.app.state, 'trusted_public_hosts', frozenset(), raising=False)
    monkeypatch.setattr(server.app.state, 'auth_required', False, raising=False)
    monkeypatch.setattr(server, '_DASHBOARD_EMBEDDED_CHAT_ENABLED', True)
    with pytest.raises(WebSocketDisconnect) as failure:
        with TestClient(server.app).websocket_connect(
            f'{route}?token={server._SESSION_TOKEN}&channel=cve-regression', headers=headers
        ):
            pytest.fail('hostile upgrade accepted')
    assert failure.value.code == 4403


@pytest.mark.parametrize('headers', [
    {'host': 'localhost:9119', 'origin': 'http://localhost:9119'},
    {'host': '127.0.0.1:9119'},
    {'host': '[::1]:9119', 'origin': 'http://[::1]:9119'},
])
def test_maintainer_loopback_and_nonbrowser_semantics_preserved(headers):
    from hermes_cli.web_server_boundary import ws_host_origin_reason
    state = SimpleNamespace(bound_host='127.0.0.1', trusted_public_hosts=frozenset())
    assert ws_host_origin_reason(SimpleNamespace(headers=headers), state) is None
