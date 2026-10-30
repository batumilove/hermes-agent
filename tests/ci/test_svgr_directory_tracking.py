"""Directory-identity controls; synthetic scanner payloads are offline fixtures."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
class DirectoryTrackingTests(unittest.TestCase):
    def test_directory_stage_and_tamper_refusal(self):
        spec = importlib.util.spec_from_file_location('tracking', ROOT/'scripts/ci/svgr_upstream_tracking.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            shutil.copytree(ROOT/'website',root/'website',ignore=shutil.ignore_patterns('node_modules','build','.docusaurus'))
            # Historical derivative controls retain their original reviewed graph.
            for name in ('package.json', 'package-lock.json'):
                (root/'website'/name).write_bytes(subprocess.check_output(['git', 'show', '862586795a9a49c798698687bdfe1f96d4dd92d9:website/'+name], cwd=ROOT))
            module.stage(root)
            slot=root/'website/node_modules/@svgr/plugin-svgo'
            slot.parent.mkdir(parents=True)
            slot.symlink_to('../../vendor/svgr-plugin-svgo')
            module.stage(root, installed=True)
            mutations=[
                ('website/package-lock.json', ('packages','node_modules/@svgr/plugin-svgo','resolved'),'attacker'),
                ('website/package.json', ('dependencies','@svgr/plugin-svgo'),'*'),
                ('website/package.json', ('overrides','@svgr/plugin-svgo'),'*'),
                ('website/vendor/svgr-plugin-svgo/TRACKING.json', ('owner',),'nobody'),
                ('website/vendor/svgr-plugin-svgo/TRACKING.json', ('agePolicy',),'waive all'),
                ('website/vendor/svgr-plugin-svgo/SOURCE.json', ('upstream','name'),'unknown'),
            ]
            for path,keys,value in mutations:
                target=root/path; original=target.read_bytes();data=json.loads(original);node=data
                for key in keys[:-1]:node=node[key]
                node[keys[-1]]=value;target.write_text(json.dumps(data), encoding="utf-8")
                try:
                    with self.subTest(path=path,keys=keys),self.assertRaises(ValueError):module.stage(root,installed=True)
                finally:target.write_bytes(original)
            slot.unlink();slot.symlink_to(root/'website')
            with self.assertRaises(ValueError):module.stage(root,installed=True)
            slot.unlink();slot.symlink_to('../../vendor/svgr-plugin-svgo')
            p=root/'website/vendor/svgr-plugin-svgo/dist/index.js'
            p.write_bytes(p.read_bytes()+b'tamper')
            with self.assertRaises(ValueError): module.stage(root,installed=True)
    def test_scanner_strict_fail_closed(self):
        spec=importlib.util.spec_from_file_location('tracking',ROOT/'scripts/ci/svgr_upstream_tracking.py')
        assert spec is not None and spec.loader is not None
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        m.validate_scan(0,{'results':[]})
        for code,payload in [(2,{'results':[]}),(0,{'results':None}),(0,{'results':[],'errors':[]}),(0,{'results':[],'unknown':1})]:
            with self.subTest(code=code,payload=payload),self.assertRaises(ValueError):m.validate_scan(code,payload)
if __name__=='__main__': unittest.main()
