"""Hosted-only PR441 cleanup adaptation: exact built image, probe-only overlay.

No application, hook, permission, or grace replacement. The derivative image is
fault-injection evidence, not the retained/scanned acceptance image.
"""
from pathlib import Path
import json
import argparse
import os
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def probe_recipe(image_id):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('invalid immutable image config ID')
    parent = 'pr439-s6-parent:' + image_id.removeprefix('sha256:')
    return f'''FROM {parent}
COPY s6_cleanup_probe.py /opt/hermes/s6_cleanup_probe.py
CMD ["/opt/hermes/.venv/bin/python", "/opt/hermes/s6_cleanup_probe.py"]
'''


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=120)


def build_probe(image_id, context, tag, output):
    # BuildKit treats FROM sha256:<config-id> as docker.io/library/sha256:<tag>.
    # Bind a local-only reference to the admitted ID and use the daemon builder.
    parent = 'pr439-s6-parent:' + image_id.removeprefix('sha256:')
    run('docker', 'tag', image_id, parent)
    for phase in ('before', 'after'):
        actual = run('docker', 'image', 'inspect', parent, '--format', '{{.Id}}').stdout.strip()
        if actual != image_id:
            raise ValueError('local probe parent identity changed')
        if phase == 'after':
            break
        command = ('docker', 'build', '--builder', 'default', '--network', 'none',
                   '--pull=false', '-t', tag, str(context))
        (output / 'build-command.json').write_text(json.dumps(command) + '\n')
        try:
            build = run(*command, check=False)
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or '', error.stderr or ''
            stdout = stdout.decode(errors='replace') if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode(errors='replace') if isinstance(stderr, bytes) else stderr
            (output / 'build.stdout.log').write_text(stdout)
            (output / 'build.stderr.log').write_text(stderr)
            (output / 'build.log').write_text(stdout + stderr)
            raise
        (output / 'build.stdout.log').write_text(build.stdout)
        (output / 'build.stderr.log').write_text(build.stderr)
        (output / 'build.log').write_text(build.stdout + build.stderr)
        build.check_returncode()


def retain_uploadable_lock(mode_output):
    # Rename only this known probe receipt in the copied evidence, never runtime data.
    # Default upload-artifact hidden-file exclusion remains enabled.
    lock = mode_output / 'update_receipts' / '.pm-write.lock'
    if lock.exists():
        lock.rename(lock.with_name('pm-write.lock.evidence'))


def stop_and_record(name, output, mode):
    start = time.monotonic()
    run('docker', 'stop', name)
    elapsed = time.monotonic() - start
    # Supplemental telemetry only: retain real terminal state without inventing
    # a new exit-code policy for deliberately adversarial timeout/stuck modes.
    terminal = run('docker', 'inspect', name)
    (output / f'{mode}-post-stop-inspect.json').write_text(terminal.stdout)
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image_id = args.image_id
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('invalid immutable image config ID')
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tag = 'pr439-s6-cleanup-contract:' + str(os.getpid())
    # Probe only: retain native /init, installed app, hook and shutdown grace.
    dockerfile = probe_recipe(image_id)
    with tempfile.TemporaryDirectory() as tmp:
        context = Path(tmp)
        (context / 'Dockerfile').write_text(dockerfile)
        probe = (source / 'tests/docker/s6_cleanup_probe.py').read_bytes()
        (context / 's6_cleanup_probe.py').write_bytes(probe)
        import hashlib
        (output / 'probe.sha256').write_text(hashlib.sha256(probe).hexdigest() + '\n')
        (output / 'Dockerfile').write_text(dockerfile)
        build_probe(image_id, context, tag, output)
    (output / 'parent-image-config-digest.txt').write_text(image_id + '\n')
    (output / 'probe-image-inspect.json').write_text(run('docker', 'image', 'inspect', tag).stdout)
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
            (output / f'{mode}-inspect.json').write_text(json.dumps(config, indent=2))
            assert not config['Mounts'] or all(m['Type'] == 'volume' for m in config['Mounts'])
            assert config['HostConfig']['NetworkMode'] == 'none'
            assert config['Config']['StopTimeout'] == 90
            elapsed = stop_and_record(name, output, mode)
            run('docker', 'cp', f'{name}:/opt/data/logs', str(output / mode))
            retain_uploadable_lock(output / mode)
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
            logs = run('docker', 'logs', name, check=False)
            (output / f'{mode}-final-container.log').write_text(logs.stdout + logs.stderr)
            run('docker', 'rm', '-f', '-v', name, check=False)


if __name__ == '__main__':
    main()
