"""Tests for gateway health snapshot metrics.

These tests guard the OTel/Prometheus exporter contract: platform health gauges
must use stable label sets so that the OpenTelemetry Prometheus exporter never
retains ghost series from transient lifecycle states.
"""

from __future__ import annotations

import pytest

from agent.monitoring.gateway_health import build_gateway_health_snapshot


def _platform_metrics(snapshot, name, platform):
    return [
        metric
        for metric in snapshot.metrics
        if metric.name == name and metric.attributes.get("hermes.platform") == platform
    ]


@pytest.mark.parametrize(
    "first_state, second_state",
    [
        ("connecting", "connected"),
        ("fatal", "connected"),
        ("disconnected", "connected"),
    ],
)
def test_platform_lifecycle_transitions_do_not_create_stale_label_series(
    first_state, second_state
):
    """ObservableGauge callbacks only return current observations.

    The OpenTelemetry Prometheus exporter retains every distinct label set it has
    ever seen for a gauge. If ``hermes.platform.up`` or
    ``hermes.platform.degraded`` include the mutable ``hermes.platform.state``
    label, a transition such as telegram ``fatal`` -> ``connected`` leaves a
    stale ``{platform=telegram,state=fatal}`` series at value 0 forever. Alert
    rules matching ``hermes_platform_up == 0`` then fire on the ghost series
    even though the platform is currently connected.

    The fix is to keep the per-platform label and drop the mutable state/error
    labels from the metric attributes. Platform state detail remains available
    in the lifecycle ``GatewayDiagnosticEvent`` events emitted by
    ``emit_runtime_status_transition``.
    """
    first_runtime = {
        "platforms": {
            "telegram": {
                "state": first_state,
                "error_code": "invalid_config" if first_state == "fatal" else None,
            },
        }
    }
    second_runtime = {
        "platforms": {
            "telegram": {"state": second_state},
        }
    }

    first = build_gateway_health_snapshot(
        first_runtime,
        gateway_running=True,
        profile="default",
        install_id="install-1",
        version="0.0.0",
        supervision_mode="manual",
    )
    second = build_gateway_health_snapshot(
        second_runtime,
        gateway_running=True,
        profile="default",
        install_id="install-1",
        version="0.0.0",
        supervision_mode="manual",
    )

    for name in ("hermes.platform.up", "hermes.platform.degraded"):
        first_series = _platform_metrics(first, name, "telegram")
        second_series = _platform_metrics(second, name, "telegram")
        assert (
            len(first_series) == 1
        ), f"expected one {name} series for telegram, got {len(first_series)}"
        assert (
            len(second_series) == 1
        ), f"expected one {name} series for telegram after transition, got {len(second_series)}"
        # Same label set across the lifecycle transition: no stale state-specific series.
        assert first_series[0].attributes == second_series[0].attributes


def test_platform_up_and_degraded_values_follow_current_state():
    runtime = {
        "platforms": {
            "telegram": {"state": "connected"},
            "webhook": {"state": "disconnected"},
            "slack": {"state": "fatal", "error_code": "invalid_config"},
        }
    }
    snapshot = build_gateway_health_snapshot(
        runtime,
        gateway_running=True,
        profile="default",
        install_id="install-1",
        version="0.0.0",
        supervision_mode="manual",
    )

    assert _platform_metrics(snapshot, "hermes.platform.up", "telegram")[0].value == 1
    assert (
        _platform_metrics(snapshot, "hermes.platform.degraded", "telegram")[0].value
        == 0
    )

    assert _platform_metrics(snapshot, "hermes.platform.up", "webhook")[0].value == 0
    assert (
        _platform_metrics(snapshot, "hermes.platform.degraded", "webhook")[0].value == 0
    )

    assert _platform_metrics(snapshot, "hermes.platform.up", "slack")[0].value == 0
    assert (
        _platform_metrics(snapshot, "hermes.platform.degraded", "slack")[0].value == 1
    )


def test_platform_health_metrics_use_only_stable_platform_label():
    """Platform health metrics must not include mutable state/error labels."""
    runtime = {
        "platforms": {
            "telegram": {"state": "connected"},
            "webhook": {"state": "connected"},
        }
    }
    snapshot = build_gateway_health_snapshot(
        runtime,
        gateway_running=True,
        profile="default",
        install_id="install-1",
        version="0.0.0",
        supervision_mode="manual",
    )

    for metric in snapshot.metrics:
        if metric.name not in ("hermes.platform.up", "hermes.platform.degraded"):
            continue
        assert "hermes.platform" in metric.attributes
        assert "hermes.platform.state" not in metric.attributes
        assert "hermes.error_code" not in metric.attributes
        # Only the platform label and base resource attributes are present.
        assert set(metric.attributes.keys()) == {
            "service.instance.id",
            "service.version",
            "hermes.supervision_mode",
            "hermes.platform",
        }


def test_degraded_platform_emits_diagnostic_event_preserving_state_and_error():
    """Removing mutable labels from gauges must not remove per-platform
    observability: fatal/degraded platforms still produce diagnostic events with
    the current state and error class.
    """
    runtime = {
        "platforms": {
            "telegram": {
                "state": "fatal",
                "error_message": "invalid config for platform",
            },
        }
    }
    snapshot = build_gateway_health_snapshot(
        runtime,
        gateway_running=True,
        profile="default",
        install_id="install-1",
        version="0.0.0",
        supervision_mode="manual",
    )

    from agent.monitoring.events import GatewayDiagnosticEvent

    diagnostics = [
        ev
        for ev in snapshot.events
        if isinstance(ev, GatewayDiagnosticEvent) and ev.name == "platform.fatal"
    ]
    assert len(diagnostics) == 1
    event = diagnostics[0]
    assert event.platform == "telegram"
    assert event.error_code == "invalid_config"
    assert event.error_class == "invalid_config"
