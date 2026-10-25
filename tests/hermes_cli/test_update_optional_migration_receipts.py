"""Optional migration PM work must not replace mandatory update preparation."""
import json

import pytest

from tests.hermes_cli import test_update_receipt_pm_embed as receipt_tests

homed = receipt_tests.homed
_isolated_receipt_state = receipt_tests._isolated_receipt_state


@pytest.mark.parametrize("migration", ["memory_provider_migration", "left_core_migration"])
def test_optional_migration_keeps_its_own_failure_receipt(homed, monkeypatch, capsys, migration):
    from hermes_cli import source_build, update_receipt
    from hermes_cli import main_install_repair, memory_provider_migration, left_core_migration
    from pm import receipt

    monkeypatch.setattr(main_install_repair, "_install_configured_features_missing_deps", lambda root: None)
    monkeypatch.setattr(source_build, "source_frontends", lambda root: ())
    monkeypatch.setattr(memory_provider_migration, "migrate_all_homes", lambda: None)
    monkeypatch.setattr(left_core_migration, "migrate_all_homes", lambda: None)
    failed = []

    def fail_install():
        receipt.begin("sync")
        receipt.record_refusal("plugin", "catalog install unavailable")
        failed.append(receipt.finalize("failed", exit_code=1))
        raise RuntimeError("catalog install unavailable")

    module = memory_provider_migration if migration == "memory_provider_migration" else left_core_migration
    monkeypatch.setattr(module, "migrate_all_homes", fail_install)
    update_receipt.begin_update_receipt()
    receipt_tests._sync_under_update(venv_rebuild={"ok": True, "reason": "mandatory preparation"})
    source_build.build_update_products(homed, desktop=False)
    terminal = json.loads(update_receipt.finalize_update_receipt("success").read_text())
    optional = json.loads(failed[0].read_text())
    assert terminal["outcome"] == "success"
    assert terminal["pm_sync_outcome"] == "ok"
    assert terminal["pm_venv_rebuild"]["reason"] == "mandatory preparation"
    assert optional["outcome"] == "failed"
    assert optional["update_id"] is None
    assert "catalog install unavailable" in capsys.readouterr().out


def test_completion_exit_agrees_with_partial_terminal_receipt(homed, monkeypatch):
    from hermes_cli import update_completion, update_receipt, update_cmd

    update_receipt.begin_update_receipt()
    request = {"receipt": dict(update_receipt._current.get().data),
               "pm_receipt": {"update_id": update_receipt.current_correlation_id(),
                              "outcome": "failed", "exit_code": 1},
               "windows_resume": {}, "gateway_mode": False}
    update_receipt._current.set(None)
    monkeypatch.setattr(update_completion, "_complete_selected", lambda request: True)
    monkeypatch.setattr(update_cmd, "_resume_windows_gateways_after_update", lambda token: None)
    result = homed / "completion.json"
    code = update_completion._finish(request, result)
    data = json.loads(result.read_text())
    assert data["receipt"]["outcome"] == "partial"
    assert code == data["exit_code"] == data["receipt"]["exit_code"] == 1
