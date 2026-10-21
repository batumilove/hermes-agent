"""Compatibility and pin regressions for the deliberately narrow candidate."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('actual', ['a' * 40, 'b' * 40, None])
def test_noop_cannot_replace_selected_pin(actual):
    source = ast.parse((ROOT / 'hermes_cli/update_cmd.py').read_text())
    node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_finish_already_up_to_date')
    completed = []
    main = SimpleNamespace(PROJECT_ROOT=ROOT, _resume_windows_gateways_after_update=lambda *a: None)
    import sys
    ns = {'_m': lambda: main, '_capture_head_sha': lambda *a: actual,
          '_complete_source_update': completed.append, '_record_stop': lambda *a: None,
          'sys': sys}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<exact-source>', 'exec'), ns)
    request = {'expected_sha': 'a' * 40, 'selected_source_sha': 'a' * 40}
    plan = SimpleNamespace(auto_stash_ref=None, parked_branch_switched=False, upstream_checked=True)
    if actual == 'a' * 40:
        ns[node.name](['git'], 'main', 'main', plan, gw_input_fn=None, completion_request=request)
        assert completed == [request]
        assert request['expected_sha'] == 'a' * 40
        return
    with pytest.raises(SystemExit):
        ns[node.name](['git'], 'main', 'main', plan, gw_input_fn=None, completion_request=request)
    assert completed == []
    assert request['expected_sha'] == 'a' * 40


def test_no_speculative_dependency_build_or_blanket_transaction_denials():
    source = (ROOT / 'hermes_cli/update_cmd.py').read_text()
    for speculative in ('prepare_target_dependencies', 'transaction_refusal', 'fleet_identity_refusal', 'unsupported_branch_switch'):
        assert speculative not in source


def test_inventory_does_not_turn_missing_ledger_home_into_fleet_denial():
    assert 'bind_runtime_scope' not in (ROOT / 'hermes_cli/update_inventory.py').read_text()
