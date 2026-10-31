"""Bounded read-only metadata acquisition; no loaded-settings attestation.

There is deliberately no supported parser profile yet. Source inventory is not
semantic closure. Never synthesize a validator receipt from these observations.
"""
import hashlib
import json
import math
import subprocess
import time

# Executed with isolated Python, without site initialization or application imports.
# No environment value is returned. Route-leaf values are hashed only after strict
# syntax checks; parent JSON, provider parameters and other overrides are unsupported.
_CONTAINER_SCRIPT = r'''
import hashlib,json,os,pathlib,re
from urllib.parse import urlsplit
out={'source_manifest':{},'parser_manifest':{},'source_presence':{},'env_input':{},'unsupported_input':False}
def digest(b): return hashlib.sha256(b).hexdigest()
def manifest(root,dest):
 root=pathlib.Path(root)
 paths=sorted(root.rglob('*.py'))
 if len(paths)>512: raise ValueError()
 total=0
 for p in paths:
  if p.is_symlink() or not p.is_file(): raise ValueError()
  b=p.read_bytes();total+=len(b)
  if len(b)>300000 or total>6000000: raise ValueError()
  dest[str(p.relative_to(root))]=digest(b)
manifest('/app/src',out['source_manifest'])
sites=list(pathlib.Path('/app/.venv/lib').glob('python*/site-packages'))
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

# Host-side stdlib only. Docker inspect explicitly selects metadata, never Env.
REMOTE_SCRIPT = '''import json,subprocess,socket
out={'hostname':socket.gethostname(),'containers':{}}
for role,name in (('api','honcho-api-1'),('deriver','honcho-deriver-1')):
 fmt='{"container_id":{{json .Id}},"image_id":{{json .Image}},"started_at":{{json .State.StartedAt}},"running":{{json .State.Running}}}'
 def run(argv):
  p=subprocess.run(argv,capture_output=True,text=True,timeout=20,check=True)
  if len(p.stdout)>262144: raise ValueError()
  return json.loads(p.stdout)
 before=run(['docker','inspect','--format',fmt,name])
 if before['running'] is not True: raise ValueError()
 data=run(['docker','exec',before['container_id'],'python','-I','-S','-c',PAYLOAD])
 after=run(['docker','inspect','--format',fmt,name])
 if before!=after: raise ValueError()
 out['containers'][role]=dict(before,**data)
print(json.dumps(out,sort_keys=True,separators=(',',':')))
'''.replace('PAYLOAD', repr(_CONTAINER_SCRIPT))


def fingerprint(value):
    """Version-1 canonical JSON SHA256 (not an authenticity signature)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def readonly_transport(script):
    """Fixed approved Teleport destination; no host/env escape hatch or retries."""
    import shlex
    proc = subprocess.run([
        'ssh', '-F', '/var/lib/tbot/hermes-cron-ssh/ssh_config',
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=5', 'ubuntu@honcho.teleport.batumi.works',
        'python3 -I -S -c ' + shlex.quote(script),
    ], capture_output=True, text=True, timeout=100, check=True)
    if len(proc.stdout) > 262144:
        raise ValueError('metadata bounds')
    return proc.stdout


def acquire_metadata(*, transport=readonly_transport, clock=time.time):
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
        samples = []
        for _ in range(2):
            raw = transport(REMOTE_SCRIPT)
            if not isinstance(raw, str) or len(raw) > 262144:
                return failure
            sample = json.loads(raw)
            if set(sample) != {'hostname', 'containers'} or sample['hostname'] != 'honcho':
                return failure
            if set(sample['containers']) != {'api', 'deriver'}:
                return failure
            for row in sample['containers'].values():
                if row['running'] is not True or type(row['unsupported_input']) is not bool:
                    return failure
                for field in ('source_manifest', 'parser_manifest', 'source_presence', 'env_input'):
                    if type(row[field]) is not dict:
                        return failure
            samples.append(sample)
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
        return dict(failure, reason='parser_source_closure_not_proven', stable=True,
                    collected_at=end, observations=observations)
    except Exception:
        # Never expose remote stderr, environment, source, or exception messages.
        return failure
