"""Offline adversarial controls, not substituted scanner execution receipts."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('tracking', ROOT/'scripts/ci/svgr_upstream_tracking.py')
assert spec is not None and spec.loader is not None
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class AdversarialTracking(unittest.TestCase):
    def test_source_lock_root_override_and_installed_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vendor = root/'website/vendor/svgr-plugin-svgo'
            shutil.copytree(ROOT/'website/vendor/svgr-plugin-svgo', vendor)
            for name in ('package.json', 'package-lock.json'):
                shutil.copyfile(ROOT/'website'/name, root/'website'/name)
            slot = root/'website/node_modules/@svgr/plugin-svgo'
            slot.parent.mkdir(parents=True)
            slot.symlink_to('../../vendor/svgr-plugin-svgo')
            self.assertTrue(m.stage(root, installed=True).is_file())
            mutations = [
                ('package.json', ['dependencies',m.NAME], '*'),
                ('package.json', ['overrides',m.NAME], '*'),
                ('package-lock.json', ['packages','','dependencies',m.NAME], '*'),
                ('package-lock.json', ['packages','node_modules/@svgr/plugin-svgo','link'], False),
                ('package-lock.json', ['packages','vendor/svgr-plugin-svgo','name'], m.NAME),
                ('package-lock.json', ['packages','vendor/svgr-plugin-svgo','version'], '9.0.0'),
                ('package-lock.json', ['packages','vendor/svgr-plugin-svgo','dependencies'], {}),
            ]
            for name, keys, value in mutations:
                p=root/'website'/name; original=p.read_bytes(); data=json.loads(original); node=data
                for key in keys[:-1]: node=node[key]
                node[keys[-1]]=value; p.write_text(json.dumps(data))
                try:
                    with self.subTest(keys=keys), self.assertRaises(ValueError): m.stage(root, installed=True)
                finally: p.write_bytes(original)
            for key in m.TRACKING:
                p=vendor/'TRACKING.json'; original=p.read_bytes(); data=json.loads(original); data[key]='changed';p.write_text(json.dumps(data))
                try:
                    with self.subTest(policy=key), self.assertRaises(ValueError):m.stage(root,installed=True)
                finally:p.write_bytes(original)
            for name in m.SOURCE['patched_sha256']:
                p=vendor/name; original=p.read_bytes(); p.write_bytes(original+b'changed')
                try:
                    with self.subTest(runtime=name), self.assertRaises(ValueError):m.stage(root,installed=True)
                finally:p.write_bytes(original)
                outside=root/'outside'; outside.write_bytes(original);p.unlink();p.symlink_to(outside)
                try:
                    with self.subTest(symlink=name), self.assertRaises(ValueError):m.stage(root,installed=True)
                finally:p.unlink();p.write_bytes(original)
            slot.unlink()
            with self.assertRaises(ValueError):m.stage(root,installed=True)
            slot.symlink_to(vendor.parent)
            with self.assertRaises(ValueError):m.stage(root,installed=True)
            slot.unlink();slot.symlink_to('../../vendor/svgr-plugin-svgo')
            self.assertTrue(m.stage(root,installed=True).is_file())

    def test_scanner_malformed_identity_error_and_positive_controls(self):
        clean={'results':[]}
        m.validate_scan(0,clean)
        package={'package':{'name':m.NAME,'version':'8.1.0','ecosystem':'npm'},'vulnerabilities':[]}
        record={'source':{'path':'.osv-lockfiles/svgr-upstream/package-lock.json','type':'lockfile'},'packages':[package]}
        valid={'results':[record]}
        m.validate_scan(0,valid)
        bad=[None, [], {}, {'results':None}, {'results':[None]}, {'results':[], 'error':'failed'}, {'results':[], 'errors':[]}, {'results':[], 'experimental_config':None}]
        for keys,value in [(['source','path'],'wrong'),(['source','type'],'unknown'),(['packages'],None),(['packages'],[]),(['packages',0,'package','name'],'other'),(['packages',0,'package','version'],'9'),(['packages',0,'package','ecosystem'],'PyPI'),(['packages',0,'vulnerabilities'],None),(['packages',0,'vulnerabilities'],[{'id':'OSV-positive-control'}]),(['packages',0,'groups'],[{}]),(['packages',0,'licenses'],None)]:
            data=copy.deepcopy(record);node=data
            for key in keys[:-1]:node=node[key]
            node[keys[-1]]=value;bad.append({'results':[data]})
        bad.append({'results':[record,record]})
        for payload in bad:
            with self.subTest(payload=payload),self.assertRaises(ValueError):m.validate_scan(0,payload)
        for code in (None,1,2,-1):
            with self.subTest(code=code),self.assertRaises(ValueError):m.validate_scan(code,clean)
        for raw in ('null','{','{"results": null}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError,json.JSONDecodeError)):
                m.validate_scan(0,json.loads(raw))

if __name__=='__main__':unittest.main()
