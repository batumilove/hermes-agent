"""Bounded read-only metadata acquisition; no loaded-settings attestation.

There is deliberately no supported parser profile yet. Source inventory is not
semantic closure. Never synthesize a validator receipt from these observations.
"""
import hashlib
import json
import math
import subprocess
import time
import os
import re
from datetime import UTC, datetime


def bounded_file(path, limit=300000):
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('not regular')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('file bounds')
        return data


def bounded_run(argv, *, timeout=20, limit=262144):
    import selectors
    import signal
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    output = bytearray()
    sizes = {'out': 0, 'err': 0}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, 'out')
            selector.register(proc.stderr, selectors.EVENT_READ, 'err')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('metadata timeout')
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fileobj.fileno(), min(8192, limit + 1 - sizes[key.data]))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    sizes[key.data] += len(chunk)
                    if sizes[key.data] > limit:
                        raise ValueError('output bounds')
                    if key.data == 'out':
                        output.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('metadata timeout')
            if proc.wait(timeout=remaining) != 0:
                raise ValueError('metadata child failed')
        return output.decode('utf-8')
    finally:
        # Kill the session even if the direct child exited but left pipe owners.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()

# Executed with isolated Python, without site initialization or application imports.
# No environment value is returned. Route-leaf values are hashed only after strict
# syntax checks; parent JSON, provider parameters and other overrides are unsupported.
_CONTAINER_SCRIPT = r'''
import hashlib,json,os,pathlib,re,stat
from urllib.parse import urlsplit
out={'source_manifest':{},'parser_manifest':{},'source_presence':{},'env_input':{},'unsupported_input':False}
def digest(b): return hashlib.sha256(b).hexdigest()
def manifest(root,dest):
 root=pathlib.Path(root)
 count=0;total=0
 pending=[root]
 while pending:
  directory=pending.pop()
  with os.scandir(directory) as entries:
   for entry in entries:
    count+=1
    if count>2048 or entry.is_symlink(): raise ValueError()
    if entry.is_dir(follow_symlinks=False):
     pending.append(pathlib.Path(entry.path));continue
    p=pathlib.Path(entry.path)
    if not entry.name.endswith('.py'): continue
    if len(dest)>=512: raise ValueError()
    b=bounded_file(str(p));total+=len(b)
    if total>6000000: raise ValueError()
    dest[str(p.relative_to(root))]=digest(b)
manifest('/app/src',out['source_manifest'])
sites=[]
for entry in pathlib.Path('/app/.venv/lib').iterdir():
 if entry.name.startswith('python') and (entry/'site-packages').is_dir():
  sites.append(entry/'site-packages')
  if len(sites)>1: raise ValueError()
if len(sites)!=1: raise ValueError()
for package in ('pydantic_settings','dotenv'):
 d={};manifest(sites[0]/package,d)
 if not d: raise ValueError()
 out['parser_manifest'].update({package+'/'+k:v for k,v in d.items()})
for p in ('/app/src/.env','/app/.env','/.env','/app/config.toml'):
 q=pathlib.Path(p);out['source_presence'][p]=q.exists() or q.is_symlink()
 if out['source_presence'][p]: out['unsupported_input']=True
prefixes=('EMBEDDING','DERIVER','SUMMARY','DREAM','DIALECTIC','LLM')
leaf=re.compile(r'(?:EMBEDDING|DERIVER|SUMMARY|DREAM_INDUCTION|DREAM_DEDUCTION)_MODEL_CONFIG__(?:MODEL|TRANSPORT|OVERRIDES__BASE_URL)|DIALECTIC_LEVELS__(?:MINIMAL|LOW|MEDIUM|HIGH|MAX)__MODEL_CONFIG__(?:MODEL|TRANSPORT|OVERRIDES__BASE_URL)|DERIVER_ROUTER_ENABLED')
for key in sorted(os.environ):
 k=key.upper()
 if not k.startswith(prefixes): continue
 # Do not read or hash credential/provider-param values.
 if any(x in k for x in ('KEY','SECRET','TOKEN','PASSWORD')): continue
 if not leaf.fullmatch(k):
  out['unsupported_input']=True;continue
 v=os.environ[key]
 if len(v)>400 or not v or any(ord(c)<32 for c in v): raise ValueError()
 if k.endswith('BASE_URL'):
  u=urlsplit(v)
  if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment: raise ValueError()
 elif not re.fullmatch(r'[A-Za-z0-9_./:-]+',v): raise ValueError()
 if k in out['env_input']: raise ValueError()
 out['env_input'][k]=digest(v.encode())
print(json.dumps(out,sort_keys=True,separators=(',',':')))
'''

import inspect
_CONTAINER_SCRIPT = inspect.getsource(bounded_file) + _CONTAINER_SCRIPT

# Host-side stdlib only. Docker inspect explicitly selects metadata, never Env.
REMOTE_SCRIPT = '''import json,subprocess,socket,os,time
BOUNDED_HELPER
out={'hostname':socket.gethostname(),'containers':{}}
for role,name in (('api','honcho-api-1'),('deriver','honcho-deriver-1')):
 fmt='{"container_id":{{json .Id}},"image_id":{{json .Image}},"started_at":{{json .State.StartedAt}},"running":{{json .State.Running}}}'
 def run(argv):
  return json.loads(bounded_run(argv))
 before=run(['docker','inspect','--format',fmt,name])
 if before['running'] is not True: raise ValueError()
 data=run(['docker','exec',before['container_id'],'python','-I','-S','-c',PAYLOAD])
 after=run(['docker','inspect','--format',fmt,name])
 if before!=after: raise ValueError()
 out['containers'][role]=dict(before,**data)
print(json.dumps(out,sort_keys=True,separators=(',',':')))
'''.replace('PAYLOAD', repr(_CONTAINER_SCRIPT)).replace('BOUNDED_HELPER', inspect.getsource(bounded_run))


def fingerprint(value):
    """Version-1 canonical JSON SHA256 (not an authenticity signature)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def readonly_transport(script, *, timeout):
    """Fixed approved Teleport destination; no host/env escape hatch or retries."""
    import shlex
    return bounded_run([
        'ssh', '-F', '/var/lib/tbot/hermes-cron-ssh/ssh_config',
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=5', 'ubuntu@honcho.teleport.batumi.works',
        'python3 -I -S -c ' + shlex.quote(script),
    ], timeout=timeout)


def acquire_metadata(*, transport=readonly_transport, clock=time.time, monotonic=time.monotonic):
    """Acquire twice, independently; return only digest observations and UNKNOWN.

    Transport is injectable for offline tests, not evidence of authenticity.
    No policy expectation is inferred from the acquired data. No configuration
    fingerprint or route fingerprint is emitted without parser completeness.
    """
    failure = {'status': 'UNSUPPORTED', 'reason': 'metadata_acquisition_failed',
               'actual_routing': 'unknown', 'probe_authorized': False,
               'receipt': None, 'observations': {}}
    try:
        start = clock()
        deadline = monotonic() + 15.0
        samples = []
        for _ in range(2):
            remaining = deadline - monotonic()
            if remaining <= 0:
                return failure
            raw = transport(REMOTE_SCRIPT, timeout=remaining)
            if not isinstance(raw, str) or len(raw) > 262144:
                return failure
            sample = json.loads(raw, object_pairs_hook=_unique)
            if set(sample) != {'hostname', 'containers'} or sample['hostname'] != 'honcho':
                return failure
            if set(sample['containers']) != {'api', 'deriver'}:
                return failure
            for row in sample['containers'].values():
                _validate_row(row)
            samples.append(sample)
        if monotonic() > deadline:
            return failure
        end = clock()
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= end - start <= 300 or samples[0] != samples[1]):
            return dict(failure, reason='collection_expired_or_changed', stable=False)
        observations = {}
        for name, row in samples[1]['containers'].items():
            observations[name] = {field + '_sha256': fingerprint(row[field]) for field in
                                  ('source_manifest', 'parser_manifest', 'source_presence', 'env_input')}
            observations[name]['metadata_sha256'] = fingerprint(row)
            observations[name]['identity'] = {k: row[k] for k in ('container_id', 'image_id', 'started_at')}
        return dict(failure, reason='parser_source_closure_not_proven', stable=True,
                    collection_started_at=start, collected_at=end, observations=observations)
    except Exception:
        # Never expose remote stderr, environment, source, or exception messages.
        return failure


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _validate_row(row):
    fields = {'container_id', 'image_id', 'started_at', 'running', 'unsupported_input',
              'source_manifest', 'parser_manifest', 'source_presence', 'env_input'}
    if type(row) is not dict or set(row) != fields:
        raise ValueError('row schema')
    _validate_identity({k: row[k] for k in ('container_id', 'image_id', 'started_at')})
    if row['running'] is not True or type(row['unsupported_input']) is not bool:
        raise ValueError('booleans')
    for field in ('source_manifest', 'parser_manifest', 'env_input'):
        value = row[field]
        if type(value) is not dict or len(value) > (1024 if field == 'parser_manifest' else 512):
            raise ValueError('map bounds')
        if field != 'env_input' and not value:
            raise ValueError('missing inventory')
        for key, digest in value.items():
            if not isinstance(key, str) or not 0 < len(key) <= 400 or not re.fullmatch(r'[A-Za-z0-9_./:-]+', key):
                raise ValueError('map key')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('digest')
    presence = row['source_presence']
    allowed = {'/app/src/.env', '/app/.env', '/.env', '/app/config.toml'}
    if type(presence) is not dict or not presence or not set(presence) <= allowed or any(type(v) is not bool for v in presence.values()):
        raise ValueError('presence')


def _validate_identity(identity):
    if type(identity) is not dict or set(identity) != {'container_id', 'image_id', 'started_at'}:
        raise ValueError('identity')
    for key, pattern in [('container_id', '[0-9a-f]{64}'), ('image_id', 'sha256:[0-9a-f]{64}'), ('started_at', r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z')]:
        if not isinstance(identity[key], str) or not re.fullmatch(pattern, identity[key]):
            raise ValueError('identity shape')
    datetime.fromisoformat(identity['started_at'])


def metadata_line(record, *, now):
    unknown = '  Metadata: UNKNOWN (missing, stale, changed or malformed observation)'
    try:
        start, end = record['collection_started_at'], record['collected_at']
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end, now)):
            return unknown
        if not 0 <= end - start <= 300 or not 0 <= now - end <= 300 or record['stable'] is not True:
            return unknown
        if record['reason'] != 'parser_source_closure_not_proven' or record['receipt'] is not None or record['probe_authorized'] is not False:
            return unknown
        rows = record['observations']
        if set(rows) != {'api', 'deriver'}:
            return unknown
        for row in rows.values():
            _validate_identity(row['identity'])
            for key in ('source_manifest_sha256', 'parser_manifest_sha256', 'source_presence_sha256', 'env_input_sha256', 'metadata_sha256'):
                if not re.fullmatch('[0-9a-f]{64}', row[key]):
                    return unknown
        stamp = datetime.fromtimestamp(end, UTC).isoformat()
        labels = '; '.join(name + '=' + rows[name]['identity']['container_id'] + '/' + rows[name]['identity']['image_id'] + '/' + rows[name]['identity']['started_at'] for name in ('api', 'deriver'))
        return '  Metadata: observed at ' + stamp + '; sampled stable; ' + labels + '; configuration interpretation UNSUPPORTED (parser_source_closure_not_proven)'
    except (KeyError, TypeError, ValueError, OverflowError):
        return unknown
