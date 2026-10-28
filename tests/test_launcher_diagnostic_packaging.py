"""Offline real GNU tar/OpenSSL regression; not Windows-native qualification."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def packaging_shell():
    text = (ROOT / '.github/workflows/windows-install-update-e2e.yml').read_text()
    block = text.split('      - name: Encrypt diagnostic evidence for local custodian\n', 1)[1]
    block = block.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0]
    return '\n'.join(line[10:] for line in block.splitlines()) + '\n'


class PackagingTest(unittest.TestCase):
    def test_real_packaging_and_failure_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='packaging-', dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            key = root / 'key.pem'
            cert = root / 'tests/launcher-diagnostic-custody.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=disposable-local-test', '-keyout', str(key), '-out', str(cert), '-days', '1'], check=True, capture_output=True)
            for mode in ('success', 'missing-input', 'invalid-cert'):
                with self.subTest(mode=mode):
                    runner = root / ('D:' + mode) / 'temp'
                    runner.mkdir(parents=True)
                    evidence = runner / 'win-update-e2e'
                    evidence.mkdir()
                    (evidence / 'receipt.txt').write_bytes(b'local synthetic diagnostic\n')
                    if mode == 'missing-input':
                        (evidence / 'receipt.txt').unlink()
                        evidence.rmdir()
                    if mode == 'invalid-cert':
                        cert.write_text('invalid certificate\n')
                    # Relative drive-colon path reproduces GNU tar remote archive parsing.
                    env = {'PATH': os.environ['PATH'], 'HOME': str(root), 'TMPDIR': str(root), 'RUNNER_TEMP': str(runner.relative_to(root)), 'EVIDENCE_ROOT': str(evidence.relative_to(root)), 'ENCRYPTED_ROOT': str((runner / 'launcher-encrypted').relative_to(root))}
                    result = subprocess.run(['bash', '--noprofile', '--norc', '-c', packaging_shell()], cwd=root, env=env, capture_output=True, text=True, timeout=20)
                    final = runner / 'launcher-encrypted/evidence.cms'
                    self.assertFalse((runner / 'launcher-evidence.tar').exists(), result.stderr)
                    self.assertFalse((runner / 'launcher-encrypted/evidence.cms.tmp').exists(), result.stderr)
                    if mode == 'success':
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue(final.is_file())
                        decrypted = subprocess.run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER', '-in', str(final), '-inkey', str(key)], check=True, capture_output=True).stdout
                        listing = subprocess.run(['tar', '-tf', '-'], input=decrypted, check=True, capture_output=True).stdout
                        self.assertIn(b'./receipt.txt', listing)
                        body = subprocess.run(['tar', '-xOf', '-', './receipt.txt'], input=decrypted, check=True, capture_output=True).stdout
                        self.assertEqual(body, b'local synthetic diagnostic\n')
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(final.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
