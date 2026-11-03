"""Real whole-container regression, runnable without pytest.

Uses an existing dependency/s6 image, overlays the exact candidate source and
reads the candidate Dockerfile's grace (not a duplicated test-only setting).
No host state/socket mounts; network disabled. Artifacts survive container exit.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=120)


def test_whole_container_cleanup(tmp_path, monkeypatch):
    """Run both slow-success and aggregate-timeout through real Docker/s6."""
    monkeypatch.setenv('S6_TEST_OUTPUT', str(tmp_path / 'evidence'))
    main()


def main():
    output = Path(os.environ['S6_TEST_OUTPUT'])
    output.mkdir(parents=True, exist_ok=False)
    grace = re.search(r'^ENV S6_KILL_GRACETIME=(\d+)$', (ROOT / 'Dockerfile').read_text(), re.MULTILINE)
    tag = 'hermes-s6-cleanup-contract:' + str(os.getpid())
    # .dockerignore excludes tests; supply the probe as a separate build context.
    dockerfile = f'''FROM hermes-agent-harness:latest
COPY . /opt/hermes/
COPY --from=probe s6_cleanup_probe.py /opt/hermes/s6_cleanup_probe.py
COPY --chmod=0755 docker/cont-finish.d/ /etc/cont-finish.d/
RUN chmod +x /opt/hermes/docker/*.sh
ENV PYTHONPATH=/opt/hermes
{f'ENV S6_KILL_GRACETIME={grace[1]}' if grace else ''}
CMD ["python", "/opt/hermes/s6_cleanup_probe.py"]
'''
    with tempfile.TemporaryDirectory() as tmp:
        recipe = Path(tmp) / 'Dockerfile'
        recipe.write_text(dockerfile)
        build = run('docker', 'build', '-f', str(recipe), '--build-context',
                    f'probe={ROOT / "tests/docker"}', '-t', tag, str(ROOT))
        (output / 'build.log').write_text(build.stdout + build.stderr)
    results = []
    for mode, delay, expected_clean in [('fast', '0', True), ('slow', '5', True), ('timeout', '120', False), ('stuck', '0', False)]:
        name = f'hermes-s6-cleanup-{os.getpid()}-{mode}'
        try:
            run('docker', 'run', '-d', '--network', 'none', '--name', name,
                '--stop-timeout', '90', '-e', f'PROBE_CLEANUP_DELAY={delay}',
                '-e', f'PROBE_STUCK={"1" if mode == "stuck" else ""}', tag)
            end = time.monotonic() + 60
            while time.monotonic() < end:
                ready = run('docker', 'exec', '-u', 'hermes', name, 'sh', '-c',
                            'test -f /opt/data/gateway_state.json && grep -q "Press Ctrl+C to stop" /opt/data/logs/gateway.log', check=False)
                if ready.returncode == 0:
                    break
                time.sleep(.5)
            else:
                raise AssertionError('gateway never ready: ' + run('docker', 'logs', name).stderr[-4000:])
            config = json.loads(run('docker', 'inspect', name).stdout)[0]
            assert not config['Mounts'] or all(m['Type'] == 'volume' for m in config['Mounts'])
            assert config['HostConfig']['NetworkMode'] == 'none'
            start = time.monotonic()
            assert config['Config']['StopTimeout'] == 90
            run('docker', 'stop', name)
            elapsed = time.monotonic() - start
            run('docker', 'cp', f'{name}:/opt/data/logs', str(output / mode))
            entered = run('docker', 'cp', f'{name}:/opt/data/post-drain-entered', str(output / f'{mode}-entered'), check=False).returncode == 0
            clean = run('docker', 'cp', f'{name}:/opt/data/.clean_shutdown', str(output / f'{mode}-clean'), check=False).returncode == 0
            logs = '\n'.join(p.read_text(errors='replace') for p in (output / mode).glob('*.log'))
            result = dict(mode=mode, elapsed=elapsed, entered=entered, clean=clean,
                          drain_done='Shutdown phase: drain done' in logs,
                          db_done='SessionDB close done' in logs,
                          stopped='Gateway stopped (total teardown' in logs,
                          incomplete='cleanup_incomplete' in logs or 'Skipping .clean_shutdown marker' in logs)
            results.append(result)
            (output / 'results.json').write_text(json.dumps(results, indent=2))
            print(result, flush=True)
            assert entered and result['drain_done'], result
            assert clean == expected_clean, result
            assert elapsed < 89, result
            container_logs = run('docker', 'logs', name)
            (output / f'{mode}-container.log').write_text(container_logs.stdout + container_logs.stderr)
            if expected_clean:
                assert result['db_done'] and result['stopped'], result
                assert elapsed < (8 if mode == 'fast' else 14), result
                if mode == 'slow':
                    assert elapsed > 5, result
            elif mode == 'stuck':
                assert elapsed > 80 and not result['stopped'], result
                assert 'gateway shutdown wait exhausted' in (output / f'{mode}-container.log').read_text(), result
            else:
                assert result['incomplete'] and result['stopped'], result
        finally:
            run('docker', 'rm', '-f', name, check=False)


if __name__ == '__main__':
    main()
