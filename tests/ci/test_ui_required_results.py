from scripts.ci.required_results import evaluate_gate


def test_selected_frontend_requires_successful_js_parent():
    detect = {'result': 'success', 'outputs': {'frontend': 'true'}}
    for result in [None, 'skipped', 'cancelled', 'failure']:
        needs = {'detect': detect}
        if result is not None:
            needs['js-tests'] = {'result': result}
        assert evaluate_gate(needs)['failed'] == ['js-tests']
    assert evaluate_gate({'detect': detect, 'js-tests': {'result': 'success'}})['ok']
