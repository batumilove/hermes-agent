"""Container-only behavioral launcher and /proc owner identity regressions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[2]


class NamedIdentity(unittest.TestCase):
    @pytest.mark.spawns_gateway_lookalike  # Import-only stub; owned child reaped below.
    def test_named_launch_and_owner_detection(self):
        from hermes_cli.service_manager import S6ServiceManager

        for supervised, inherited_profile in ((True, False), (True, True), (False, False)):
            with self.subTest(supervised=supervised, inherited_profile=inherited_profile), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'custom-root'
                home = root / 'profiles' / 'worker'
                home.mkdir(parents=True)
                executable = Path(tmp) / 'hermes'
                # Execute real CLI import-time profile resolution, but never
                # connect a platform/provider or start the application loop.
                (home / 'config.yaml').write_text('{}\n')
                executable.write_text('#!' + sys.executable + '\nimport os,time\nfrom pathlib import Path\nimport hermes_cli.main\nPath(os.environ["READY"]).write_text(os.environ["HERMES_HOME"])\ntime.sleep(30)\n')
                executable.chmod(0o755)
                ready = Path(tmp) / 'ready'
                env = dict(os.environ, HERMES_HOME=str(root), READY=str(ready), PATH=tmp + ':' + os.environ['PATH'])
                if inherited_profile:
                    env['HERMES_HOME'] = str(root / 'profiles' / 'caller')
                if supervised:
                    script = S6ServiceManager._render_run_script('worker', {})
                    # Strip container uid/cwd setup only; execute the actual rendered exports/exec.
                    script = script.replace('export HOME=/opt/data', '').replace('cd /opt/data', '')
                    script = script.replace('[ "$(id -u)" = 0 ] || ', '').split('exec s6-setuidgid')[0]
                    command = ['sh', '-c', script]
                else:
                    command = [str(executable), '-p', 'worker', 'gateway', 'run', '--no-supervise']
                child = subprocess.Popen(command, env=env)
                try:
                    end = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < end:
                        time.sleep(.02)
                    self.assertTrue(ready.exists())
                    self.assertEqual(ready.read_text(), str(home))
                    spec = importlib.util.spec_from_file_location('wait_gateway', ROOT / 'docker/wait-gateway-stop.py')
                    assert spec and spec.loader
                    helper = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(helper)
                    state = dict(kind='hermes-gateway', pid=child.pid, start_time=helper.fingerprint(child.pid))
                    (home / 'gateway_state.json').write_text(json.dumps(state))
                    subprocess.run([sys.executable, str(ROOT / 'docker/wait-gateway-stop.py')], env=dict(env, HERMES_HOME=str(root)), check=True, timeout=5)
                    try:
                        child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.fail('verified named gateway was omitted and remains alive')
                    self.assertEqual(child.returncode, -15)
                    if supervised:
                        # The launcher itself must bind the resolved custom-root home pre-exec.
                        self.assertIn('export HERMES_HOME=', script)
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait()

    @pytest.mark.spawns_gateway_lookalike  # Sleeping argv stubs; each child reaped below.
    def test_foreground_near_matches_do_not_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'profiles' / 'worker'
            home.mkdir(parents=True)
            script = root / 'hermes'
            script.write_text('import time; time.sleep(30)')
            spec = importlib.util.spec_from_file_location('wait_gateway', ROOT / 'docker/wait-gateway-stop.py')
            assert spec and spec.loader
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            for argv, initial_home in [
                (['-p', 'other', 'gateway', 'run', '--no-supervise'], root),
                (['-p', 'worker', 'chat', 'run', '--no-supervise'], root),
                (['-p', 'worker', 'gateway', 'run', '--no-supervise', 'extra'], root),
                (['-p', 'worker', 'gateway', 'run', '--no-supervise'], root / 'wrong'),
            ]:
                with self.subTest(argv=argv, initial_home=initial_home):
                    child = subprocess.Popen([sys.executable, str(script), *argv], env=dict(os.environ, HERMES_HOME=str(initial_home)))
                    try:
                        (home / 'gateway_state.json').write_text(json.dumps(dict(kind='hermes-gateway', pid=child.pid, start_time=helper.fingerprint(child.pid))))
                        subprocess.run([sys.executable, str(ROOT / 'docker/wait-gateway-stop.py')], env=dict(os.environ, HERMES_HOME=tmp), check=True, timeout=3)
                        self.assertIsNone(child.poll())
                    finally:
                        child.kill()
                        child.wait()

    def test_packaged_foreground_owner_requires_exact_bootstrap_and_identity(self):
        from unittest.mock import patch

        # Exact /proc/cmdline bootstrap observed in the packaged image, not
        # sys.argv after the bootstrap has rewritten argv[0]. Never execute it.
        bootstrap = ("import os, site, sys; sys.argv[0]='hermes'; "
                     "site.addsitedir(os.environ['HERMES_SITE']); "
                     "from hermes_cli.main import main; sys.exit(main())")
        prefix = ['/opt/hermes/.venv/bin/python', '-P', '-c', bootstrap]
        tail = ['-p', 'probe', 'gateway', 'run', '--no-supervise']
        spec = importlib.util.spec_from_file_location('wait_gateway', ROOT / 'docker/wait-gateway-stop.py')
        assert spec and spec.loader
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'custom-root'
            home = root / 'profiles' / 'probe'
            home.mkdir(parents=True)
            cases = [
                ('packaged', prefix + tail, root, 123, 'hermes-gateway', 185, True),
                ('long-profile', prefix + ['--profile', *tail[1:]], root, 123, 'hermes-gateway', 185, True),
                ('script', ['/usr/bin/python3', '/bin/hermes', *tail], root, 123, 'hermes-gateway', 185, True),
                ('arbitrary-code', prefix[:3] + ['pass'] + tail, root, 123, 'hermes-gateway', 185, False),
                ('altered-code', prefix[:3] + [bootstrap + '; pass'] + tail, root, 123, 'hermes-gateway', 185, False),
                ('missing-safe-path', [prefix[0], '-c', bootstrap, *tail], root, 123, 'hermes-gateway', 185, False),
                ('non-python', ['/bin/other', *prefix[1:], *tail], root, 123, 'hermes-gateway', 185, False),
                ('wrong-profile', prefix + ['-p', 'other', *tail[2:]], root, 123, 'hermes-gateway', 185, False),
                ('wrong-root', prefix + tail, root / 'wrong', 123, 'hermes-gateway', 185, False),
                ('missing-no-supervise', prefix + tail[:-1], root, 123, 'hermes-gateway', 185, False),
                ('extra-tail', prefix + tail + ['extra'], root, 123, 'hermes-gateway', 185, False),
                ('stale-ticks', prefix + tail, root, 124, 'hermes-gateway', 185, False),
                ('wrong-kind', prefix + tail, root, 123, 'other', 185, False),
                ('pid-one', prefix + tail, root, 123, 'hermes-gateway', 1, False),
            ]
            for name, argv, initial_home, ticks, kind, pid, accepted in cases:
                with self.subTest(case=name):
                    (home / 'gateway_state.json').write_text(json.dumps(dict(kind=kind, pid=pid, start_time=ticks)))
                    signaled = []

                    def proc_bytes(path):
                        if str(path) == f'/proc/{pid}/environ':
                            return os.fsencode('HERMES_HOME=' + str(initial_home)) + b'\0'
                        if str(path) == f'/proc/{pid}/cmdline':
                            return b'\0'.join(os.fsencode(a) for a in argv) + b'\0'
                        raise AssertionError(f'unexpected proc read: {path}')

                    # Virtual /proc and signal sink: no real process can be
                    # signaled. After TERM the virtual owner disappears.
                    with patch.dict(os.environ, HERMES_HOME=str(root)), \
                         patch.object(Path, 'read_bytes', proc_bytes), \
                         patch.object(helper, 'fingerprint', side_effect=lambda p: None if signaled else 123), \
                         patch.object(helper.os, 'kill', side_effect=lambda p, s: signaled.append((p, s))):
                        helper.main()
                    self.assertEqual(signaled, [(pid, 15)] if accepted else [])

    def test_invalid_owner_records_do_not_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], env=dict(os.environ, HERMES_HOME=tmp))
            try:
                spec = importlib.util.spec_from_file_location('wait_gateway', ROOT / 'docker/wait-gateway-stop.py')
                assert spec and spec.loader
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                ticks = helper.fingerprint(child.pid)
                for record in [dict(kind='hermes-gateway', pid=child.pid, start_time=ticks + 1), dict(kind='other', pid=child.pid, start_time=ticks), dict(kind='hermes-gateway', pid=1, start_time=ticks)]:
                    (home / 'gateway_state.json').write_text(json.dumps(record))
                    subprocess.run([sys.executable, str(ROOT / 'docker/wait-gateway-stop.py')], env=dict(os.environ, HERMES_HOME=tmp), check=True, timeout=3)
                    self.assertIsNone(child.poll())
                # Matching PID/start ticks but wrong named profile/argv is not ownership.
                named = home / 'profiles' / 'worker'
                named.mkdir(parents=True)
                (home / 'gateway_state.json').unlink()
                (named / 'gateway_state.json').write_text(json.dumps(dict(kind='hermes-gateway', pid=child.pid, start_time=ticks)))
                subprocess.run([sys.executable, str(ROOT / 'docker/wait-gateway-stop.py')], env=dict(os.environ, HERMES_HOME=tmp), check=True, timeout=3)
                self.assertIsNone(child.poll())
            finally:
                child.kill()
                child.wait()


if __name__ == '__main__':
    unittest.main()
