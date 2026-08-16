"""Tests for the external drain-control marker contract + gateway state machine.

Task 2.2/2.3. Two layers:
  * drain_control.py — the presence-based marker contract (write/clear/read,
    HERMES_HOME-scoped, never-raises).
  * GatewayRunner enter/exit/watcher + the new-turn accept gate — the
    reversible state machine driven by the marker.

Mocked tests are necessary-not-sufficient here (the HARD live-validation gate,
Q-B, exercises a real `hermes gateway run`); these lock the unit contract.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gateway.drain_control as dc
from gateway.run import GatewayRunner, _publish_authoritative_startup_status
from gateway.status import read_runtime_status
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


# ---------------------------------------------------------------------------
# Marker contract (drain_control.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


class TestMarkerContract:
    def test_absent_by_default(self, home):
        assert dc.drain_requested() is False
        assert dc.read_drain_request() is None

    def test_write_then_present(self, home):
        payload = dc.write_drain_request(principal="nas")
        assert dc.drain_requested() is True
        assert payload["action"] == "drain"
        assert payload["principal"] == "nas"
        body = dc.read_drain_request()
        assert body is not None and body["principal"] == "nas"


class TestOwnedDrainControl:
    """Regression coverage for the competing-controller marker race."""

    @pytest.fixture(autouse=True)
    def _isolated_activation_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "run-user" / "hermes-gateway-activation.lock"
        monkeypatch.setattr(dc, "activation_lock_path", lambda: lock_path)

    def test_public_ownership_apis_cannot_override_canonical_lock_path(self):
        for func in (
            dc.acquire_drain_ownership,
            dc.write_drain_request,
            dc.clear_drain_request,
            dc.activation_lock_held,
        ):
            assert "runtime_dir" not in inspect.signature(func).parameters

    def test_default_lock_path_is_exact_per_user_runtime_path(self, monkeypatch):
        monkeypatch.undo()
        assert dc.activation_lock_path() == Path(
            f"/run/user/{os.getuid()}/hermes-gateway-activation.lock"
        )

    def test_owned_transaction_excludes_competing_controller(self, home):
        owner = dc.acquire_drain_ownership(
            principal="activation-a",
            home=home,
            owner_token="transaction-a",
        )
        try:
            payload = owner.write_request()
            assert payload["owner_token"] == "transaction-a"

            with pytest.raises(dc.DrainControlBusyError):
                dc.acquire_drain_ownership(
                    principal="activation-b",
                    home=home,
                    owner_token="transaction-b",
                )
            with pytest.raises(dc.DrainControlBusyError):
                dc.write_drain_request(
                    principal="dashboard",
                    home=home,
                )
            with pytest.raises(dc.DrainControlBusyError):
                dc.clear_drain_request(home=home)

            assert owner.assert_request_owned()["owner_token"] == "transaction-a"
        finally:
            owner.clear_request()
            owner.release()

        assert dc.read_drain_request(home=home) is None

    def test_operator_can_clear_orphan_after_owner_releases_lock(self, home):
        owner = dc.acquire_drain_ownership(
            principal="activation-a",
            home=home,
            owner_token="transaction-a",
        )
        owner.write_request()
        owner.release()  # simulate controller exit without compare-and-delete

        assert dc.clear_drain_request(home=home) is True
        assert dc.read_drain_request(home=home) is None

    def test_refresh_fails_closed_when_marker_is_replaced(self, home):
        owner = dc.acquire_drain_ownership(
            principal="activation-a",
            home=home,
            owner_token="transaction-a",
        )
        try:
            owner.write_request()
            replacement = dc.read_drain_request(home=home)
            assert replacement is not None
            replacement["owner_token"] = "transaction-b"
            dc.drain_request_path(home).write_text(json.dumps(replacement), encoding="utf-8")

            with pytest.raises(dc.DrainOwnershipLostError):
                owner.refresh_request()
            with pytest.raises(dc.DrainOwnershipLostError):
                owner.clear_request()
            assert dc.read_drain_request(home=home)["owner_token"] == "transaction-b"
        finally:
            owner.release()

    def test_refresh_fails_closed_when_marker_is_removed(self, home):
        owner = dc.acquire_drain_ownership(
            principal="activation-a",
            home=home,
            owner_token="transaction-a",
        )
        try:
            owner.write_request()
            dc.drain_request_path(home).unlink()

            with pytest.raises(dc.DrainOwnershipLostError):
                owner.assert_request_owned()
            with pytest.raises(dc.DrainOwnershipLostError):
                owner.refresh_request()
        finally:
            owner.release()

    def test_lock_keeps_gateway_drained_if_owned_marker_disappears(self, home):
        owner = dc.acquire_drain_ownership(
            principal="activation-a",
            home=home,
            owner_token="transaction-a",
        )
        try:
            owner.write_request()
            dc.drain_request_path(home).unlink()
            assert dc.drain_requested(home=home) is True
        finally:
            owner.release()

        assert dc.drain_requested(home=home) is False


class TestSuppressNotification:
    """The generic suppress_notification flag on the drain marker.

    Gates ONLY the gateway's home-channel shutdown broadcast (NAS auto-update
    sets it true). Default-false so legacy/operator drains behave as before.
    The reader reuses the NS-570 epoch-staleness check so an orphaned marker
    can never silence a fresh gateway.
    """

    def test_default_false(self, home):
        payload = dc.write_drain_request(principal="nas")
        assert payload["suppress_notification"] is False
        assert dc.drain_notification_suppressed() is False

    def test_flag_round_trips_true(self, home):
        payload = dc.write_drain_request(principal="nas", suppress_notification=True)
        assert payload["suppress_notification"] is True
        body = dc.read_drain_request()
        assert body is not None and body["suppress_notification"] is True
        assert dc.drain_notification_suppressed() is True


# ---------------------------------------------------------------------------
# Instantiation-epoch staleness (NS-570: orphaned marker on durable volume)
# ---------------------------------------------------------------------------


class TestInstantiationEpoch:
    def test_write_stamps_current_epoch(self, home):
        payload = dc.write_drain_request(principal="nas")
        assert payload["epoch"] == dc.current_instantiation_epoch()
        body = dc.read_drain_request()
        assert body is not None and body["epoch"] == dc.current_instantiation_epoch()


    def test_marker_from_prior_instantiation_reads_as_absent(self, home, monkeypatch):
        # THE NS-570 REGRESSION. A begin-drain marker written by a PREVIOUS
        # container/VM instantiation survives on the durable HERMES_HOME volume
        # across a machine restart. The freshly-restarted gateway (new epoch)
        # must treat it as absent, NOT re-engage drain.
        monkeypatch.setattr(dc, "current_instantiation_epoch", lambda: "epoch-OLD")
        dc.write_drain_request(principal="nas")  # stamps "epoch-OLD"
        assert dc.drain_requested() is True  # same epoch → active

        # Simulate the restart: a brand-new instantiation epoch.
        monkeypatch.setattr(dc, "current_instantiation_epoch", lambda: "epoch-NEW")
        # The marker file is still physically present on the volume…
        assert dc.drain_request_path().exists() is True
        # …but it is ignored because its epoch belongs to a prior instantiation.
        assert dc.drain_requested() is False


    def test_current_epoch_empty_when_proc_unreadable(self, monkeypatch):
        # When neither /proc identity source is readable, the epoch is "" so
        # the staleness check is disabled rather than crashing.
        from pathlib import Path as _P

        orig_read_text = _P.read_text

        def _boom(self, *a, **k):
            if str(self).startswith("/proc/"):
                raise OSError("no /proc")
            return orig_read_text(self, *a, **k)

        dc.current_instantiation_epoch.cache_clear()
        monkeypatch.setattr(_P, "read_text", _boom)
        try:
            assert dc.current_instantiation_epoch() == ""
        finally:
            dc.current_instantiation_epoch.cache_clear()


# ---------------------------------------------------------------------------
# Authoritative startup ownership/readiness
# ---------------------------------------------------------------------------


class _StartupStatusRunner:
    def __init__(self, *, shutdown_requested=False):
        self._external_drain_active = False
        self._shutdown_requested = shutdown_requested

    def _startup_should_abort(self):
        return self._shutdown_requested


class TestAuthoritativeStartupStatus:
    def test_pid_owner_acknowledges_inherited_drain_before_slow_startup(self, home):
        dc.write_drain_request(principal="activation-controller")
        runner = _StartupStatusRunner()

        state = _publish_authoritative_startup_status(runner, default_state="starting")

        payload = read_runtime_status()
        assert state == "draining"
        assert runner._external_drain_active is True
        assert payload["pid"] == os.getpid()
        assert payload["gateway_state"] == "draining"

    def test_startup_without_drain_publishes_pid_bound_starting(self, home):
        runner = _StartupStatusRunner()

        state = _publish_authoritative_startup_status(runner, default_state="starting")

        payload = read_runtime_status()
        assert state == "starting"
        assert runner._external_drain_active is False
        assert payload["pid"] == os.getpid()
        assert payload["gateway_state"] == "starting"

    def test_slow_startup_cannot_overwrite_inherited_drain_with_running(self, home):
        dc.write_drain_request(principal="activation-controller")
        runner = _StartupStatusRunner()
        _publish_authoritative_startup_status(runner, default_state="starting")

        state = _publish_authoritative_startup_status(runner, default_state="running")

        assert state == "draining"
        assert read_runtime_status()["gateway_state"] == "draining"

    def test_shutdown_precedence_cannot_be_resurrected_as_running(self, home):
        runner = _StartupStatusRunner(shutdown_requested=True)

        state = _publish_authoritative_startup_status(runner, default_state="running")

        assert state == "stopping"
        assert read_runtime_status()["gateway_state"] == "stopping"


# ---------------------------------------------------------------------------
# requested_at max-age (#85433: same-epoch orphaned marker, no restart)
# ---------------------------------------------------------------------------


class TestMarkerMaxAge:
    def test_fresh_marker_honoured(self, home):
        dc.write_drain_request(principal="nas")
        assert dc.drain_requested() is True

    def test_expired_marker_reads_as_absent(self, home):
        # THE #85433 REGRESSION. A drain-gated action completes WITHOUT a
        # machine restart, so the epoch still matches — but the writer never
        # cancelled the drain. The orphan must not wedge the gateway forever.
        from datetime import datetime, timedelta, timezone

        dc.write_drain_request(principal="nas", suppress_notification=True)
        body = dc.read_drain_request()
        assert body is not None
        body["requested_at"] = (
            datetime.now(timezone.utc)
            - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + 60)
        ).isoformat()
        dc.drain_request_path().write_text(json.dumps(body), encoding="utf-8")

        # The marker file is still physically present, with the CURRENT epoch…
        assert dc.drain_request_path().exists() is True
        # …but it is ignored because it outlived any legitimate drain.
        assert dc.drain_requested() is False
        # The suppression flag of an expired orphan is likewise ignored.
        assert dc.drain_notification_suppressed() is False

    def test_marker_without_timestamp_still_honoured(self, home):
        # Leniency contract: no requested_at (legacy/corrupt body) must fail
        # toward quiescing, exactly like the epoch check.
        payload = {"action": "drain", "epoch": dc.current_instantiation_epoch()}
        dc.drain_request_path().write_text(json.dumps(payload), encoding="utf-8")
        assert dc.drain_requested() is True

    def test_unparseable_timestamp_still_honoured(self, home):
        payload = {
            "action": "drain",
            "epoch": dc.current_instantiation_epoch(),
            "requested_at": "not-a-timestamp",
        }
        dc.drain_request_path().write_text(json.dumps(payload), encoding="utf-8")
        assert dc.drain_requested() is True

    def test_naive_timestamp_treated_as_utc(self, home):
        # A writer that stamped a tz-naive ISO string must still expire.
        from datetime import datetime, timedelta, timezone

        stale_naive = (
            datetime.now(timezone.utc)
            - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + 60)
        ).replace(tzinfo=None)
        payload = {
            "action": "drain",
            "epoch": dc.current_instantiation_epoch(),
            "requested_at": stale_naive.isoformat(),
        }
        dc.drain_request_path().write_text(json.dumps(payload), encoding="utf-8")
        assert dc.drain_requested() is False

    def test_expiry_warning_logged_once_per_marker(self, home, caplog):
        # The watcher polls every 1s; an expired orphan must warn ONCE, not
        # once per tick (~86k/day). A refreshed marker that expires again
        # warns again (new requested_at).
        import logging
        from datetime import datetime, timedelta, timezone

        def _write_expired(offset_seconds):
            dc.write_drain_request(principal="nas")
            body = dc.read_drain_request()
            assert body is not None
            body["requested_at"] = (
                datetime.now(timezone.utc)
                - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + offset_seconds)
            ).isoformat()
            dc.drain_request_path().write_text(json.dumps(body), encoding="utf-8")

        _write_expired(60)
        with caplog.at_level(logging.WARNING, logger="gateway.drain_control"):
            assert dc.drain_requested() is False
            assert dc.drain_requested() is False  # second poll tick
            assert dc.drain_requested() is False  # third poll tick
        expired_logs = [r for r in caplog.records if "expired drain marker" in r.message]
        assert len(expired_logs) == 1

        caplog.clear()
        _write_expired(120)  # a DIFFERENT requested_at that is also expired
        with caplog.at_level(logging.WARNING, logger="gateway.drain_control"):
            assert dc.drain_requested() is False
        expired_logs = [r for r in caplog.records if "expired drain marker" in r.message]
        assert len(expired_logs) == 1

    def test_rewrite_refreshes_the_clock(self, home):
        # The sanctioned keep-alive: re-writing the marker bumps requested_at,
        # so a deliberately long drain stays honoured.
        from datetime import datetime, timedelta, timezone

        dc.write_drain_request(principal="nas")
        body = dc.read_drain_request()
        assert body is not None
        body["requested_at"] = (
            datetime.now(timezone.utc)
            - timedelta(seconds=dc.DRAIN_REQUEST_MAX_AGE_SECONDS + 60)
        ).isoformat()
        dc.drain_request_path().write_text(json.dumps(body), encoding="utf-8")
        assert dc.drain_requested() is False  # expired…
        dc.write_drain_request(principal="nas")  # …keep-alive re-write
        assert dc.drain_requested() is True


# ---------------------------------------------------------------------------
# Gateway state machine (enter / exit / idempotency)
# ---------------------------------------------------------------------------


def _drain_runner():
    runner, adapter = make_restart_runner()
    runner._external_drain_active = False
    # Bind the real methods under test.
    runner._enter_external_drain = GatewayRunner._enter_external_drain.__get__(
        runner, GatewayRunner
    )
    runner._exit_external_drain = GatewayRunner._exit_external_drain.__get__(
        runner, GatewayRunner
    )
    return runner, adapter


class TestDrainStateMachine:


    def test_enter_idempotent(self):
        runner, _ = _drain_runner()
        runner._enter_external_drain()
        runner._update_runtime_status.reset_mock()
        runner._enter_external_drain()  # second call — no-op
        runner._update_runtime_status.assert_not_called()


    def test_exit_during_shutdown_does_not_revert_to_running(self):
        runner, _ = _drain_runner()
        runner._enter_external_drain()
        runner._update_runtime_status.reset_mock()
        # A shutdown drain is now in progress — exit must NOT resurrect running.
        runner._draining = True
        runner._exit_external_drain()
        assert runner._external_drain_active is False
        runner._update_runtime_status.assert_not_called()


# ---------------------------------------------------------------------------
# Watcher reconciliation
# ---------------------------------------------------------------------------


class TestDrainWatcher:

    @pytest.mark.asyncio
    async def test_watcher_enters_then_exits_with_marker(self, home):
        runner, _ = _drain_runner()
        runner._drain_control_watcher = GatewayRunner._drain_control_watcher.__get__(
            runner, GatewayRunner
        )
        # Drive a few ticks manually rather than spinning the loop.
        dc.write_drain_request()
        task = asyncio.create_task(runner._drain_control_watcher(interval=0.02))
        await asyncio.sleep(0.06)
        assert runner._external_drain_active is True
        dc.clear_drain_request()
        await asyncio.sleep(0.06)
        assert runner._external_drain_active is False
        runner._running = False
        await asyncio.sleep(0.04)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# New-turn accept gate
# ---------------------------------------------------------------------------


class TestNewTurnGate:
    @pytest.mark.asyncio
    async def test_new_turn_refused_during_external_drain(self):
        runner, _ = _drain_runner()
        runner._external_drain_active = True
        event = MessageEvent(
            text="hello",
            message_type=MessageType.TEXT,
            source=make_restart_source(),
            message_id="m1",
        )
        result = await runner._handle_message(event)
        assert result is not None
        assert "draining" in result.lower()

