"""Fork CI applicability, dependency preparation, and aggregate verdict contracts."""
from pathlib import Path

import pytest
import yaml

from scripts.ci.required_results import evaluate_gate

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/ci.yaml'


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_ci_runs_review_and_live_candidates():
    triggers = _workflow().get('on', _workflow().get(True))
    assert {'pull_request', 'workflow_dispatch'} <= triggers.keys()
    assert 'batumi/live' in triggers['push']['branches']


def test_required_aggregate_includes_all_fork_gates():
    aggregate = _workflow()['jobs']['all-checks-pass']
    assert {'fork-delta', 'fork-contracts', 'osv-scanner'} <= set(aggregate['needs'])
    assert aggregate['if'] == 'always()'
    assert aggregate['name'] == 'All required checks pass'


@pytest.mark.parametrize('result', ['skipped', 'cancelled', 'failure'])
def test_selected_fork_contracts_fail_closed(result):
    needs = {
        'detect': {'result': 'success', 'outputs': {'fork_contracts': 'true'}},
        'fork-delta': {'result': 'success'},
        'fork-contracts': {'result': result},
    }
    assert evaluate_gate(needs)['failed'] == ['fork-contracts']


def test_selected_fork_gate_cannot_disappear():
    needs = {'detect': {'result': 'success', 'outputs': {'fork_contracts': 'true'}}}
    assert evaluate_gate(needs)['failed'] == ['fork-contracts', 'fork-delta']


def test_fork_contracts_use_isolated_pm_test_environment():
    steps = _workflow()['jobs']['fork-contracts']['steps']
    setup = next(i for i, step in enumerate(steps) if step.get('uses') == './.github/actions/setup-pm')
    run = next(i for i, step in enumerate(steps) if step.get('run') == 'python scripts/run_batumi_contracts.py -j 4')
    assert setup < run
    assert steps[setup]['with']['test-environment'] == 'true'
    assert 'messaging' in steps[setup]['with']['extras']


def test_fork_delta_has_complete_history_for_accepted_merge():
    steps = _workflow()['jobs']['fork-delta']['steps']
    checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['fetch-depth'] == 0
    run = next(step['run'] for step in steps if 'python scripts/check_fork_delta.py' in step.get('run', ''))
    assert 'https://github.com/NousResearch/hermes-agent.git' in run
    assert 'git merge-base --is-ancestor' in run
    assert run.rstrip().endswith('python scripts/check_fork_delta.py')
