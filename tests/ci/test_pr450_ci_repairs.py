"""Regressions for the three completed PR450 CI failures."""
import ast
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CIRepairTests(unittest.TestCase):
    def test_tracking_json_uses_explicit_utf8(self):
        tree = ast.parse((ROOT / 'scripts/ci/svgr_upstream_tracking.py').read_text(encoding='utf-8-sig'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {'read_text', 'write_text'}]
        self.assertEqual(len(calls), 7)
        for call in calls:
            assert isinstance(call.func, ast.Attribute)
            with self.subTest(line=call.lineno):
                self.assertTrue(any(k.arg == 'encoding' and isinstance(k.value, ast.Constant) and k.value.value == ('utf-8-sig' if call.func.attr == 'read_text' else 'utf-8') for k in call.keywords))

    def test_scanners_use_runner_uid_readonly_source(self):
        workflow = (ROOT / '.github/workflows/osv-scanner.yml').read_text(encoding='utf-8-sig')
        self.assertEqual(workflow.count('--user "$(id -u):$(id -g)"'), 3)
        self.assertEqual(workflow.count('-v "$PWD:/src:ro"'), 3)
        self.assertEqual(workflow.count('-v "$RUNNER_TEMP/osv-output:/output"'), 3)
        self.assertIn('--fail-on-vuln=true', workflow)
        self.assertNotIn('chmod 777', workflow)

    def test_qualification_alias_preserves_automation_identity(self):
        result = subprocess.run(['git', 'check-mailmap', 'Local Qualification <local-qualification@invalid>'], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        self.assertEqual(result.stdout.strip(), 'Hermes Agent <hermes@local>')
        self.assertTrue((ROOT / 'contributors/emails/local-qualification@invalid').is_file())


if __name__ == '__main__':
    unittest.main()
