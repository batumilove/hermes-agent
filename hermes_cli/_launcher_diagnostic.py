"""Opt-in bounded launcher observer. No environment/exception text is retained.

Raw launcher bytes and paths are sensitive fixture evidence, not public reports.
Never execute retained bytes. This module performs filesystem reads/writes only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 96
_count = 0


def enabled():
    return os.environ.get('HERMES_LAUNCHER_DIAGNOSTIC') == '1'


def snapshot(home, repo, phase, *, enabled=False):
    if not enabled or phase not in ('before', 'after', 'teardown'):
        return
    try:
        home, repo = Path(home), Path(repo)
        dest = home / 'launcher-diagnostic'
        dest.mkdir(mode=0o700, exist_ok=True)
        files = []
        for label, directory in (('user-bin', home / 'bin'), ('checkout-bin', repo / '.hermes' / 'bin')):
            for path in sorted(directory.iterdir()) if directory.is_dir() else ():
                if (path.name not in ('hermes', 'hermes-acp') and not path.name.startswith(('hermes.', 'hermes-acp.'))) or path.is_symlink():
                    continue
                if len(files) >= 24:
                    break
                row: dict = {'directory': label, 'name': path.name, 'path': str(path)}
                try:
                    with path.open('rb') as stream:
                        data = stream.read(MAX_BYTES + 1)
                    row['bytes'] = path.stat().st_size
                    if len(data) <= MAX_BYTES:
                        row['sha256'] = hashlib.sha256(data).hexdigest()
                        (dest / f'{phase}-{label}-{len(files)}.bin').write_bytes(data)
                    else:
                        row['overflow'] = True
                except Exception as exc:
                    row['error_class'] = type(exc).__name__
                files.append(row)
        (dest / f'{phase}.json').write_text(json.dumps({'files': files}, indent=2), encoding='utf-8')
    except Exception:
        pass


def event(kind, **fields):
    """Allowlisted metadata; no repr(), exception messages, env or arbitrary payload."""
    global _count
    if not enabled() or _count >= MAX_EVENTS:
        return
    try:
        from hermes_constants import get_default_hermes_root
        dest = get_default_hermes_root() / 'launcher-diagnostic'
        dest.mkdir(mode=0o700, exist_ok=True)
        _count += 1
        allowed = {'python', 'store', 'own_store', 'source', 'resource', 'filename', 'exception_class', 'available', 'reason', 'status', 'bytes', 'sha256'}
        row = {'pid': os.getpid(), 'sequence': _count, 'kind': kind}
        row.update({k: str(v)[:2048] for k, v in fields.items() if k in allowed})
        if _count == MAX_EVENTS:
            row = {'pid': os.getpid(), 'sequence': _count, 'kind': 'overflow', 'truncated': True}
        with (dest / f'events-{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
    except Exception:
        pass


def store_inventory(repo, python):
    if not enabled():
        return
    try:
        from pm.environments import store_root
        from hermes_constants import get_default_hermes_root
        dest = get_default_hermes_root() / 'launcher-diagnostic'
        dest.mkdir(mode=0o700, exist_ok=True)

        def capture(path):
            # Callers supply only fixed facts/resource names; never follow facts entries.
            row = {'source': path}
            try:
                if path.is_symlink():
                    return {**row, 'status': 'read-error', 'exception_class': 'SymlinkRefused'}
                with path.open('rb') as stream:
                    size = os.fstat(stream.fileno()).st_size
                    if size > MAX_BYTES:
                        return {**row, 'status': 'oversize', 'bytes': size}
                    data = stream.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    return {**row, 'status': 'oversize', 'bytes': len(data)}
                digest = hashlib.sha256(data).hexdigest()
                resource = hashlib.sha256(str(path).encode()).hexdigest() + '-' + digest + '.bin'
                (dest / resource).write_bytes(data)
                return {**row, 'status': 'ok', 'bytes': len(data), 'sha256': digest, 'resource': resource}
            except FileNotFoundError:
                return {**row, 'status': 'missing'}
            except Exception as exc:
                return {**row, 'status': 'read-error', 'exception_class': type(exc).__name__}

        event('store', python=python, store=store_root(repo), own_store=store_root(repo, honor_runtime_override=False))
        for label, store in (('selected', store_root(repo)), ('own', store_root(repo, honor_runtime_override=False))):
            row = capture(store / 'facts.json')
            event('facts-' + label, **row)
            if row['status'] == 'ok':
                try:
                    entry = json.loads((dest / row['resource']).read_bytes().decode('utf-8-sig')).get('packages', {}).get('python', {}).get('entry')
                    event('python-entry-' + label, python=entry)
                except Exception as exc:
                    event('facts-parse-error', exception_class=type(exc).__name__)
        package = Path(python).parent / 'Lib' / 'site-packages' / 'pip' / '_vendor' / 'distlib'
        for name in ('__init__.py', 'scripts.py', 'resources.py', 't64.exe', 't32.exe', 't64-arm.exe'):
            event('vendored-resource', **capture(package / name))
    except Exception as exc:
        event('inventory-error', exception_class=type(exc).__name__)
