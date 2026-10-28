"""PR437 isolated diagnostic overlay; assertions and token mechanism remain unchanged."""
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest


def snapshot(machine, phase):
    result = {'phase': phase, 'head': machine.head, 'target': machine.next, 'bins': {}, 'stores': []}
    for label, folder in [('home', machine.hermes_home / 'bin'), ('checkout', machine.install_dir / '.hermes' / 'bin')]:
        rows = []
        if folder.is_dir():
            for item in sorted(folder.iterdir()):
                row = {'name': item.name, 'is_file': item.is_file()}
                if item.is_file():
                    try:
                        data = item.read_bytes()
                        row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
                    except OSError as error:
                        row['error_class'] = type(error).__name__
                rows.append(row)
        result['bins'][label] = rows
    for facts in [machine.hermes_home / 'tools' / 'facts.json', *machine.hermes_home.glob('installs/*/facts.json')]:
        if facts.is_file():
            data = json.loads(facts.read_text(encoding='utf-8-sig'))
            python = (data.get('packages') or {}).get('python') or {}
            result['stores'].append({'path': str(facts), 'sha256': hashlib.sha256(facts.read_bytes()).hexdigest(), 'python_entry': python.get('entry')})
    (machine.logs / ('pr437-' + phase + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')


def install_hooks(machine):
    hook = Path(__file__).with_name('pr437_trace_hook.py')
    installed = []
    # Instrument real already-installed interpreters, not the test interpreter.
    for python in [*machine.hermes_home.glob('tools/**/python.exe'), *machine.hermes_home.glob('installs/**/python.exe')]:
        if python.parent.name.lower() == 'scripts':
            site = python.parent.parent / 'Lib' / 'site-packages'
        else:
            site = python.parent / 'Lib' / 'site-packages'
        if not site.is_dir():
            continue
        shutil.copyfile(hook, site / 'pr437_trace_hook.py')
        (site / 'pr437_diagnostic.pth').write_text('import pr437_trace_hook\n', encoding='utf-8')
        installed.append(str(python))
    (machine.logs / 'pr437-hook-interpreters.json').write_text(json.dumps(installed, indent=2), encoding='utf-8')


def retain(machine):
    traces = [*machine.hermes_home.glob('tools/**/pr437-launcher-trace.jsonl'), *machine.hermes_home.glob('installs/**/pr437-launcher-trace.jsonl')]
    for number, trace in enumerate(traces):
        shutil.copyfile(trace, machine.logs / f'pr437-launcher-trace-{number}.jsonl')


@pytest.fixture(scope='session', autouse=True)
def diagnostic_overlay():
    from tests.e2e.core.windows_update._machine import Machine
    original_install, original_update, original_teardown = Machine.install, Machine.update, Machine.teardown

    def install(self, *args, **kwargs):
        result = original_install(self, *args, **kwargs)
        snapshot(self, 'after-old-install')
        install_hooks(self)
        return result

    def update(self, *args, **kwargs):
        snapshot(self, 'before-update')
        try:
            return original_update(self, *args, **kwargs)
        finally:
            snapshot(self, 'after-update')
            retain(self)

    def teardown(self, *args, **kwargs):
        snapshot(self, 'before-teardown')
        retain(self)
        return original_teardown(self, *args, **kwargs)

    Machine.install, Machine.update, Machine.teardown = install, update, teardown
    yield
    Machine.install, Machine.update, Machine.teardown = original_install, original_update, original_teardown
