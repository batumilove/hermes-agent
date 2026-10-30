"""Mandatory installed provenance gate in the actual PR/weekly workflow."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class InstalledWorkflowGate(unittest.TestCase):
    def test_actual_shared_pr_weekly_workflow_requires_clean_installed_binding(self):
        workflow = (ROOT / '.github/workflows/osv-scanner.yml').read_text()
        self.assertIn('workflow_call:', workflow)
        self.assertIn('schedule:', workflow)
        self.assertIn('node-version: 26.12.1', workflow)
        self.assertIn('npm install --global npm@12.1.0 --ignore-scripts', workflow)
        self.assertIn('npm --prefix website ci --ignore-scripts --audit=false --fund=false', workflow)
        self.assertIn('python3 scripts/ci/svgr_upstream_tracking.py stage --installed', workflow)
        self.assertLess(workflow.index('ci --ignore-scripts'), workflow.index('stage --installed'))
        self.assertLess(workflow.index('stage --installed'), workflow.index('Enforce original SVGR upstream advisory scan'))

if __name__ == '__main__':
    unittest.main()
