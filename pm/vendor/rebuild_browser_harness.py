"""Rebuild the pinned upstream wheel with only Pillow dependency metadata patched."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

URL = "https://files.pythonhosted.org/packages/4a/bd/f166cf9a465eff436048d25851ab28b0d798af02b56998127a4165227fc4/browser_harness-0.1.13-py3-none-any.whl"
SHA256 = "2491459e4bfc0ee8aea22dc6c4680fc0f791b7ba553446323c50d2883449d769"
OLD = "browser_harness-0.1.13.dist-info"
NEW = "browser_harness-0.1.13+hermes.1.dist-info"


def rebuild():
    payload = urllib.request.urlopen(URL, timeout=60).read()
    if hashlib.sha256(payload).hexdigest() != SHA256:
        raise ValueError("Upstream wheel digest mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
        entries = {n.replace(OLD, NEW): wheel.read(n) for n in wheel.namelist() if not n.endswith('/RECORD')}
    key = NEW + '/METADATA'
    metadata = entries[key].decode()
    assert metadata.count('Version: 0.1.13\n') == 1
    assert metadata.lower().count('requires-dist: pillow==12.3.0\n') == 1
    metadata = metadata.replace('Version: 0.1.13\n', 'Version: 0.1.13+hermes.1\n')
    metadata = metadata.replace('Requires-Dist: pillow==12.3.0\n', 'Requires-Dist: pillow==13.0.0\n')
    entries[key] = metadata.encode()
    record = io.StringIO()
    writer = csv.writer(record, lineterminator='\n')
    for name, data in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256=' + digest, len(data)))
    writer.writerow((NEW + '/RECORD', '', ''))
    entries[NEW + '/RECORD'] = record.getvalue().encode()
    output = Path(__file__).with_name('browser_harness-0.1.13+hermes.1-py3-none-any.whl')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 21, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            wheel.writestr(info, data)
    print(output.name, hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    rebuild()
