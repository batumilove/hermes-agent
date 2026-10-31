import io
import json
import sys
import time
from unittest.mock import patch
import pytest
from hermes_cli import honcho_config_acquisition as a
from tests.hermes_cli.test_honcho_config_acquisition import packet

# Restart impact: restart_free; isolated candidate only; no activation.
# Rollback identity: 06ce3746efd89a80e52167a719ae3d2b30bd318c.

@pytest.mark.parametrize('role', ['api', 'deriver'])
@pytest.mark.parametrize('field', ['container_id', 'image_id', 'started_at'])
@pytest.mark.parametrize('value', [None, '', 12, False, [], {}])
def test_identity_rejected(role, field, value):
    p = packet()
    if value is None:
        del p['containers'][role][field]
    else:
        p['containers'][role][field] = value
    r = a.acquire_metadata(transport=lambda _, **kwargs: json.dumps(p), clock=lambda: 2000)
    assert r.get('stable') is not True
    assert r['observations'] == {}

@pytest.mark.parametrize('field,value', [('extra', True), ('source_manifest', {}), ('parser_manifest', {}), ('env_input', {'x': 'bad'}), ('source_presence', {'/app/config.toml': 'false'})])
def test_malformed_stable_packet(field, value):
    p = packet()
    p['containers']['api'][field] = value
    assert a.acquire_metadata(transport=lambda _, **kwargs: json.dumps(p))['observations'] == {}

def test_duplicate_keys_rejected():
    raw = json.dumps(packet()).replace('"hostname": "honcho"', '"hostname":"bad","hostname":"honcho"')
    assert a.acquire_metadata(transport=lambda _, **kwargs: raw)['observations'] == {}

def test_read_is_limit_plus_one():
    class Stream(io.BytesIO):
        def fileno(self):
            return 123
        def close(self):
            self.position_at_close = self.tell()
            super().close()
    stream = Stream(b'x' * 20)
    with patch.object(a.os, 'open', return_value=123), patch.object(a.os, 'fstat', return_value=type('S', (), {'st_mode': 0o100600})()), patch.object(a.os, 'fdopen', return_value=stream):
        with pytest.raises(ValueError):
            a.bounded_file('unused', 10)
        assert stream.position_at_close == 11

@pytest.mark.parametrize('code', ["import os; os.write(1,b'x'*1000000)", "import os; os.write(2,b'x'*1000000)", 'import time; time.sleep(5)'])
def test_bounded_child(code):
    start = time.monotonic()
    with pytest.raises((ValueError, TimeoutError)):
        a.bounded_run([sys.executable, '-c', code], timeout=.2, limit=1024)
    assert time.monotonic() - start < 2

def test_fresh_report_and_ttl():
    r = a.acquire_metadata(transport=lambda _, **kwargs: json.dumps(packet()), clock=lambda: 2000)
    assert 'observed' in a.metadata_line(r, now=2000)
    assert 'api=' in a.metadata_line(r, now=2000)
    for now in (1999, 2301):
        assert 'UNKNOWN' in a.metadata_line(r, now=now)
    assert 'UNKNOWN' in a.metadata_line(dict(r, stable=False), now=2000)
    assert 'UNKNOWN' in a.metadata_line({}, now=2000)

def test_report_collects_once_pair_not_legacy():
    from hermes_cli import honcho_monitor as m
    calls = []
    def transport(script, **kwargs):
        calls.append(script)
        return json.dumps(packet())
    with patch.object(m, 'collect_snapshot', side_effect=AssertionError('legacy forbidden')):
        text = m.collect_metadata_report(transport=transport, clock=lambda: 2000)
    assert len(calls) == 2
    assert 'Metadata: observed' in text
    assert 'Inspected configuration: UNKNOWN' in text
    assert 'Actual request routing: UNKNOWN' in text
    assert 'probe_authorized=false' in text
