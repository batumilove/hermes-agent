"""PR437 isolated diagnostic overlay; assertions and token mechanism remain unchanged."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

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
    original_advance = Machine.advance

    def advance(self):
        # Private origin only: instrument the target blob before ANY target interpreter starts.
        # This synthetic successor is provenance-only, never acceptance of the product SHA.
        from tests.e2e.core.windows_update._machine import REAL_GIT
        import runpy
        assert REAL_GIT is not None
        instrument = runpy.run_path(str(Path(__file__).with_name('pr437_trace_hook.py')))['instrument_source']
        def git(*args, input=None, env=None):
            return subprocess.run([REAL_GIT, '-C', str(self.serve), *args], input=input,
                                  capture_output=True, check=True, timeout=120, env=env).stdout
        exact = self.next
        rel = 'hermes_cli/_launchers.py'
        source = git('show', f'{exact}:{rel}').decode('utf-8')
        edited = instrument(source, self.logs / 'pr437-target-trace.jsonl').encode('utf-8')
        blob = git('hash-object', '-w', '--stdin', input=edited).decode().strip()
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_INDEX_FILE=str(self.root / 'diagnostic.index'),
                   GIT_AUTHOR_NAME='Hermes diagnostic', GIT_AUTHOR_EMAIL='diagnostic@hermes.invalid',
                   GIT_COMMITTER_NAME='Hermes diagnostic', GIT_COMMITTER_EMAIL='diagnostic@hermes.invalid')
        git('read-tree', exact, env=env)
        git('update-index', '--cacheinfo', f'100644,{blob},{rel}', env=env)
        tree = git('write-tree', env=env).decode().strip()
        self.next = git('commit-tree', tree, '-p', exact, '-m', 'diagnostic-only target publication trace', env=env).decode().strip()
        (self.logs / 'pr437-target-binding.json').write_text(json.dumps({
            'product_parent': exact, 'diagnostic_target': self.next, 'tree': tree,
            'original_launcher_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'instrumented_launcher_sha256': hashlib.sha256(edited).hexdigest(),
            'acceptance': False}, indent=2), encoding='utf-8')
        return original_advance(self)

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

    Machine.advance = advance
    Machine.install, Machine.update, Machine.teardown = install, update, teardown
    yield
    Machine.advance = original_advance
    Machine.install, Machine.update, Machine.teardown = original_install, original_update, original_teardown
