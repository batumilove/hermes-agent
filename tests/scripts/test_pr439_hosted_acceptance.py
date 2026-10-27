"""Offline contract tests for the nonpublishing PR439 acceptance control plane."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/ci/pr439_hosted_acceptance.py'

class Acceptance(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'acceptance implementation missing')
        spec = importlib.util.spec_from_file_location('acceptance', SCRIPT)
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_exact_source_only(self):
        m = self.module
        self.assertEqual(m.admit(m.SOURCE, m.TREE), (m.SOURCE, m.TREE))
        for value in ['main', m.SOURCE.upper(), m.SOURCE + '\n', 'a' * 40, '$(id)', '']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.admit(value, m.TREE)
        with self.assertRaises(ValueError):
            m.admit(m.SOURCE, 'b' * 40)

    def test_identity_mismatch_blocks(self):
        m = self.module
        stamp = {'commit': m.SOURCE, 'dirty': False, 'packageVersion': '0.21.5+1.g' + m.SOURCE}
        marker = {'revision': m.SOURCE, 'version': stamp['packageVersion']}
        m.verify_identity(stamp, marker, stamp['packageVersion'])
        for damaged in [dict(stamp, commit='a'*40), dict(stamp, dirty=True), dict(stamp, packageVersion='0.0.0')]:
            with self.assertRaises(ValueError):
                m.verify_identity(damaged, marker, stamp['packageVersion'])
        with self.assertRaises(ValueError):
            m.verify_identity(stamp, dict(marker, revision='a'*40), stamp['packageVersion'])

    def test_workflow_boundary(self):
        import yaml
        w = yaml.safe_load((ROOT / '.github/workflows/pr439-hosted-acceptance.yml').read_text())
        self.assertEqual(w['permissions'], {})
        job = w['jobs']['acceptance']
        self.assertEqual(job['permissions'], {'contents': 'read'})
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        text = (ROOT / '.github/workflows/pr439-hosted-acceptance.yml').read_text()
        for forbidden in ['pull_request_target:', 'secrets.', 'packages: write', 'id-token:', 'docker push', 'push: true', 'continue-on-error', 'gh release', 'cache-to:']:
            self.assertNotIn(forbidden, text)
        for required in ['--ignore-unfixed', '--severity HIGH,CRITICAL', '--pkg-types os,library', '--exit-code 1', '--network none', 'persist-credentials: false', 'retention-days: 14', 'if: always()']:
            self.assertIn(required, text)

if __name__ == '__main__':
    unittest.main()
