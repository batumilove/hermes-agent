"""Retired dependency APIs never report readiness or modify runtime packages."""

import pytest

from tools import lazy_deps


@pytest.mark.parametrize("feature", ["observability.langfuse", "tool.computer_use", "unknown"])
def test_retired_ensure_refuses_until_managed_relaunch(feature):
    with pytest.raises(ImportError, match="relaunch Hermes"):
        lazy_deps.ensure(feature, prompt=False)


def test_retired_installer_cannot_mutate_an_ordinary_runtime(monkeypatch):
    monkeypatch.setattr(lazy_deps, "in_historical_update", lambda: False)
    monkeypatch.setattr(lazy_deps, "stop_for_relaunch", lambda: pytest.fail("ordinary runtime attempted update takeover"))
    with pytest.raises(ImportError, match="retired"):
        lazy_deps.install_specs(["langfuse==4.15.2"])
