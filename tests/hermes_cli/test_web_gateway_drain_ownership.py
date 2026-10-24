"""HTTP receipts for activation-lock ownership and storage failures."""
import pytest


class TestGatewayDrainOwnership:
    @pytest.fixture(autouse=True)
    def _setup_test_client(self, monkeypatch, _isolate_hermes_home):
        """Create a TestClient and isolate the state DB under the test HERMES_HOME."""
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/starlette not installed")

        import hermes_state
        from hermes_constants import get_hermes_home
        from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

        monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")

        self.client = TestClient(app)
        self.client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

    def test_gateway_drain_conflict_returns_409(self, monkeypatch):
        from gateway import drain_control

        def _busy(**kwargs):
            raise drain_control.DrainControlBusyError("activation lock held")

        monkeypatch.setattr(drain_control, "write_drain_request", _busy)
        resp = self.client.post("/api/gateway/drain", json={"action": "drain"})
        assert resp.status_code == 409
        assert "activation lock held" in resp.json()["detail"]

    def test_gateway_drain_cancel_conflict_returns_409(self, monkeypatch):
        from gateway import drain_control

        def _busy(**kwargs):
            raise drain_control.DrainControlBusyError("activation lock held")

        monkeypatch.setattr(drain_control, "clear_drain_request", _busy)
        resp = self.client.post("/api/gateway/drain", json={"action": "cancel"})
        assert resp.status_code == 409
        assert "activation lock held" in resp.json()["detail"]

    @pytest.mark.parametrize("action", ["drain", "cancel"])
    def test_gateway_drain_unavailable_returns_503(self, monkeypatch, action):
        from gateway import drain_control

        def _unavailable(**kwargs):
            raise drain_control.DrainControlUnavailableError("lock storage unavailable")

        target = "write_drain_request" if action == "drain" else "clear_drain_request"
        monkeypatch.setattr(drain_control, target, _unavailable)

        resp = self.client.post("/api/gateway/drain", json={"action": action})

        assert resp.status_code == 503
        assert "lock storage unavailable" in resp.json()["detail"]
