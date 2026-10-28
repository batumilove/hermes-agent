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

    def test_reconciled_binding_is_literal_and_old_identity_rejected(self):
        import yaml
        source = '381d8e15a0f568c3a2e21be3dff99d221595a3c0'
        tree = 'b7a5493e92968857f2143cbec0e7221e6f83558f'
        m = self.module
        self.assertEqual((m.SOURCE, m.TREE), (source, tree))
        self.assertEqual(m.admit(source, tree), (source, tree))
        stale = [
            ('1c26b6d9d74901a9ffdcc8c197946ed388b32836', '2c787f4eea7914550bdab7cdf68fa4d0307540ab'),
            ('f47b8970ae32abc11c1f16f4464de40d3c41e09f', '2dacb83a3c8c5d1959f69d64df1d500592076f9d'),
        ]
        for old_source, old_tree in stale:
            for rejected in [(old_source, old_tree), (old_source, tree), (source, old_tree)]:
                with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                    m.admit(*rejected)
        w = yaml.safe_load((ROOT / '.github/workflows/pr439-hosted-acceptance.yml').read_text())
        events = w.get('on', w.get(True))
        self.assertEqual(events['workflow_dispatch']['inputs']['source_sha']['default'], source)
        self.assertEqual(w['jobs']['acceptance']['env'], {'SOURCE_SHA': "${{ inputs.source_sha || '" + source + "' }}", 'SOURCE_TREE': tree})

    def test_s6_uses_exact_built_image_and_preserves_native_runtime(self):
        import yaml
        w = yaml.safe_load((ROOT / '.github/workflows/pr439-hosted-acceptance.yml').read_text())
        steps = w['jobs']['acceptance']['steps']
        s6 = next((s for s in steps if s['name'] == 'Exercise incoming s6 cleanup on exact built image'), None)
        self.assertIsNotNone(s6, 'incoming PR441 whole-container cleanup must be exercised')
        assert s6 is not None
        self.assertIn('control/scripts/ci/pr439_s6_acceptance.py', s6['run'])
        self.assertIn('evidence/image-config-digest.txt', s6['run'])
        harness = (ROOT / 'scripts/ci/pr439_s6_acceptance.py').read_text()
        self.assertIn('FROM {image_id}', harness)
        self.assertNotIn('COPY . /opt/hermes', harness)
        self.assertNotIn('COPY --chmod', harness)
        for mode in ["('fast', '0', True)", "('slow', '5', True)", "('timeout', '120', False)", "('stuck', '0', False)"]:
            self.assertIn(mode, harness)
        self.assertIn("assert 'gateway shutdown wait exhausted'", harness)
        self.assertIn("config['HostConfig']['NetworkMode'] == 'none'", harness)
        self.assertIn('all(m[\'Type\'] == \'volume\'', harness)
        spec = importlib.util.spec_from_file_location('s6_acceptance', ROOT / 'scripts/ci/pr439_s6_acceptance.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        image_id = 'sha256:' + 'a' * 64
        recipe = module.probe_recipe(image_id)
        self.assertEqual(recipe.splitlines()[0], 'FROM ' + image_id)
        self.assertEqual(len(recipe.splitlines()), 3)
        for invalid in ['latest', image_id + '\nRUN id', 'sha256:' + 'A' * 64]:
            with self.assertRaises(ValueError):
                module.probe_recipe(invalid)

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

    def test_import_objects_prepared_before_offline_stamp(self):
        import yaml
        w = yaml.safe_load((ROOT / '.github/workflows/pr439-hosted-acceptance.yml').read_text())
        steps = w['jobs']['acceptance']['steps']
        prepare = next(s['run'] for s in steps if s['name'] == 'Verify checkout and prepare existing authenticated stamp')
        self.assertIn('fetch --refetch --no-filter --no-tags origin "$import_source"', prepare)
        self.assertIn('timeout 120s git -C source', prepare)
        self.assertLess(prepare.index('fetch --refetch'), prepare.index('python3 -m scripts.build.image_identity'))
        self.assertLess(prepare.index('status --porcelain'), prepare.index('fetch --refetch'))
        self.assertNotIn('filter: blob:none', (ROOT / '.github/workflows/pr439-hosted-acceptance.yml').read_text())

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
