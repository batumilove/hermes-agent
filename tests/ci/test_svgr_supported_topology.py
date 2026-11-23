"""Supported-site reconciliation retains historical scanning, rejects mixed graphs."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('tracking_supported', ROOT/'scripts/ci/svgr_upstream_tracking.py')
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class SupportedTopology(unittest.TestCase):
    def test_supported_graph_and_partial_removal_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT/'website/vendor', root/'website/vendor')
            for name in ('package.json', 'package-lock.json'):
                shutil.copyfile(ROOT/'website'/name, root/'website'/name)
            self.assertTrue(m.stage(root, installed=True).is_file())
            for name, key, value in [
                ('package.json', 'overrides', {m.NAME:'$'+m.NAME}),
                ('package-lock.json', 'packages', {'node_modules/@svgr/webpack': {'version':'8.1.0'}}),
            ]:
                p=root/'website'/name; original=p.read_bytes(); data=json.loads(original)
                data[key].update(value);p.write_text(json.dumps(data), encoding="utf-8")
                try:
                    with self.subTest(name=name), self.assertRaises(ValueError):m.stage(root, installed=True)
                finally:p.write_bytes(original)
            slot=root/'website/node_modules/@svgr/plugin-svgo'
            slot.parent.mkdir(parents=True);slot.symlink_to('/missing')
            with self.assertRaises(ValueError):m.stage(root, installed=True)
