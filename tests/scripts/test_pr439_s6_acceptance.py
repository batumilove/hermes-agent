"""Offline subprocess-boundary regression tests; no Docker daemon required."""
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('s6', ROOT / 'scripts/ci/pr439_s6_acceptance.py')
assert spec is not None and spec.loader is not None
s6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s6)
IMAGE = 'sha256:' + 'a' * 64


class BuildEvidence(unittest.TestCase):
    def test_main_failure_preserves_docker_diagnostics(self):
        def command(*args, **kwargs):
            result = subprocess.CompletedProcess(args, 19, 'diagnostic stdout\n', 'diagnostic stderr\n')
            if args[:2] != ('docker', 'build'):
                return subprocess.CompletedProcess(args, 0, IMAGE + '\n', '')
            if kwargs.get('check', True):
                result.check_returncode()
            return result
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            probe = root / 'source/tests/docker/s6_cleanup_probe.py'
            probe.parent.mkdir(parents=True)
            probe.write_text('print("fixture")\n')
            out = root / 'evidence'
            argv = ['s6', '--image-id', IMAGE, '--source', str(root / 'source'), '--output', str(out)]
            with patch('sys.argv', argv), patch.object(s6, 'run', side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):
                    s6.main()
            self.assertTrue((out / 'build.log').exists(), 'failed Docker build lost diagnostics')
            self.assertEqual((out / 'build.log').read_text(), 'diagnostic stdout\ndiagnostic stderr\n')

    def test_build_failure_retains_both_streams_and_blocks(self):
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('docker', 'build'):
                return subprocess.CompletedProcess(args, 23, 'build stdout\n', 'build stderr\n')
            return subprocess.CompletedProcess(args, 0, IMAGE + '\n', '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(s6, 'run', side_effect=command):
            out = pathlib.Path(tmp)
            with self.assertRaises(subprocess.CalledProcessError) as error:
                s6.build_probe(IMAGE, out, 'pr439-s6-test:1', out)
            self.assertEqual(error.exception.returncode, 23)
            self.assertEqual((out / 'build.log').read_text(), 'build stdout\nbuild stderr\n')
            self.assertEqual((out / 'build.stdout.log').read_text(), 'build stdout\n')
            self.assertEqual((out / 'build.stderr.log').read_text(), 'build stderr\n')
            self.assertFalse(any(c[:2] == ('docker', 'run') for c in calls))

    def test_local_parent_binding_and_no_pull_build(self):
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, IMAGE + '\n' if args[:3] == ('docker', 'image', 'inspect') else '', '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(s6, 'run', side_effect=command):
            out = pathlib.Path(tmp)
            s6.build_probe(IMAGE, out, 'pr439-s6-test:1', out)
        parent = 'pr439-s6-parent:' + 'a' * 64
        self.assertIn(('docker', 'tag', IMAGE, parent), calls)
        self.assertEqual(s6.probe_recipe(IMAGE).splitlines()[0], 'FROM ' + parent)
        build = next(c for c in calls if c[:2] == ('docker', 'build'))
        self.assertIn('--pull=false', build)
        self.assertEqual(build[build.index('--network') + 1], 'none')
        self.assertEqual(build[build.index('--builder') + 1], 'default')
        self.assertEqual(sum(c[:3] == ('docker', 'image', 'inspect') for c in calls), 2)

    def test_timeout_retains_partial_streams_and_blocks(self):
        def command(*args, **kwargs):
            if args[:2] == ('docker', 'build'):
                raise subprocess.TimeoutExpired(args, 120, output=b'partial stdout', stderr=b'partial stderr')
            return subprocess.CompletedProcess(args, 0, IMAGE + '\n', '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(s6, 'run', side_effect=command):
            out = pathlib.Path(tmp)
            with self.assertRaises(subprocess.TimeoutExpired):
                s6.build_probe(IMAGE, out, 'pr439-s6-test:1', out)
            self.assertEqual((out / 'build.log').read_text(), 'partial stdoutpartial stderr')

    def test_parent_mismatch_blocks_build(self):
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, 'sha256:' + 'b' * 64 + '\n', '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(s6, 'run', side_effect=command):
            with self.assertRaises(ValueError):
                s6.build_probe(IMAGE, pathlib.Path(tmp), 'pr439-s6-test:1', pathlib.Path(tmp))
        self.assertFalse(any(c[:2] == ('docker', 'build') for c in calls))


if __name__ == '__main__':
    unittest.main()
