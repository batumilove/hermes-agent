"""Bounded metadata-only full reconciliation into a newly owned private stage."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

from hermes_cli.insights_metadata_read import MESSAGE_FIELDS, SESSION_COLUMNS, read_scope


class _PrivateStage:
    """Linux descriptor-bound output; never traverse a replacement on cleanup.

    Same-uid processes must not mutate entries inside the mode-0700 stage.
    Ancestor and stage renames are tolerated safely but invalidate success.
    """

    def __init__(self, path):
        self.path = path
        self.fds = []
        self.edges = []
        self.created = False
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            self.fds.append(fd)
            for name in path.parts[1:-1]:
                info = os.fstat(fd)
                if info.st_uid not in (0, os.geteuid()) or (
                    info.st_mode & 0o020 and info.st_gid != os.getegid()
                    and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
                ) or (
                    info.st_mode & 0o002 and not
                    (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
                ):
                    raise ValueError('untrusted stage ancestor')
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
                self.edges.append((fd, name, child))
                self.fds.append(child)
                fd = child
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError('stage parent must be private to the caller')
            self.parent = fd
            self.name = path.name
            os.mkdir(self.name, 0o700, dir_fd=fd)
            child = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            self.fds.append(child)
            self.edges.append((fd, self.name, child))
            self.directory = child
            info = os.fstat(child)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('stage is not private')
            self.created = True
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        for parent, name, child in self.edges:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            owned = os.fstat(child)
            if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                raise ValueError('stage path identity changed')

    def write(self, payload):
        self.check()
        fd = os.open('metadata-summary.db', os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(fd)
            self.check()
        finally:
            os.close(fd)

    def discard(self):
        # No enumeration, no unlink: an incomplete artifact is retained on I/O
        # failure rather than risking an unrelated replacement file.
        if self.created:
            try:
                self.check()
                os.rmdir(self.name, dir_fd=self.parent)
            except (OSError, ValueError):
                return

    def close(self):
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds.clear()


def refresh_stage(args):
    from hermes_constants import get_hermes_home

    if not (1 <= args.max_rows <= 100_000 and 1 <= args.max_bytes <= 64 * 1024 * 1024
            and 0 < args.seconds <= 30):
        raise ValueError('invalid budget')
    if not re.fullmatch('[0-9a-f]{64}', args.schema_sha256):
        raise ValueError('invalid pin')
    source = (Path(get_hermes_home()) / 'state.db').resolve(strict=True)
    # No caller-selected DB filename, no overwrite or promotion operation.
    stage = Path(args.stage_dir).expanduser().absolute()
    owner = _PrivateStage(stage)
    out = None
    deadline = time.monotonic() + args.seconds
    rows = byte_count = 0
    counts = [0, 0]
    highwater = 0

    def charge(row):
        nonlocal rows, byte_count
        rows += 1
        byte_count += len(json.dumps(row, ensure_ascii=True, default=str).encode())
        if rows > args.max_rows or byte_count > args.max_bytes or time.monotonic() >= deadline:
            raise ValueError('budget exhausted')

    try:
        with read_scope(source, deadline) as reader:
            schema = list(reader.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY type,name"))
            version = reader.execute('PRAGMA user_version').fetchone()[0]
            fingerprint = hashlib.sha256(json.dumps({'user_version': version, 'objects': schema}, separators=(',', ':'), default=str).encode()).hexdigest()
            if fingerprint != args.schema_sha256:
                raise ValueError('schema mismatch')
            observed = time.time()
            owner.check()
            out = sqlite3.connect(':memory:', timeout=0.05)
            out.executescript(Path(__file__).with_name('insights_summary_schema.sql').read_text())
            out.execute('PRAGMA journal_mode=DELETE')
            out.execute('PRAGMA synchronous=FULL')
            out.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            out.execute('BEGIN')
            for row in reader.execute('SELECT ' + SESSION_COLUMNS + ' FROM sessions ORDER BY id'):
                charge(row)
                digest = hashlib.sha256(json.dumps(row, separators=(',', ':'), default=str).encode()).hexdigest()
                out.execute('INSERT INTO summary_sessions (' + SESSION_COLUMNS + ',fingerprint) VALUES (' + ','.join('?' for _ in range(len(row)+1)) + ')', (*row, digest))
                counts[0] += 1
            for row in reader.execute('SELECT ' + MESSAGE_FIELDS + ' FROM messages ORDER BY id'):
                charge(row)
                out.execute('INSERT INTO summary_message_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?)', row)
                counts[1] += 1
                highwater = row[0]
            out.execute('INSERT INTO summary_session_roles SELECT session_id,role,count(*) FROM summary_message_metadata GROUP BY session_id,role')
            out.execute("INSERT INTO summary_session_tools SELECT session_id,tool_name,count(*),min(timestamp),max(timestamp) FROM summary_message_metadata WHERE tool_name IS NOT NULL AND tool_name!='' GROUP BY session_id,tool_name")
            meta = {'source_schema_sha256': fingerprint, 'bootstrap_complete': '1',
                    'summary_generated_at': str(time.time()), 'source_observed_at': str(observed),
                    'committed_message_high_water': str(highwater), 'source_message_high_water': str(highwater),
                    'projection_scope': 'exact sessions and message metadata; skills unavailable; private stage only'}
            out.executemany('INSERT INTO summary_meta VALUES (?,?)', meta.items())
            if time.monotonic() >= deadline:
                raise ValueError('deadline')
            out.commit()
            owner.write(out.serialize())
            out.close()
            out = None
        return {'complete': True, 'sessions': counts[0], 'messages': counts[1],
                'schema_sha256': fingerprint, 'message_high_water': highwater,
                'projection_bytes': byte_count, 'scope': meta['projection_scope']}
    except BaseException:
        if out is not None:
            out.close()
        owner.discard()
        raise
    finally:
        owner.close()


def cmd_refresh_stage(args):
    try:
        result = refresh_stage(args)
    except (OSError, ValueError, sqlite3.Error):
        print('Insights private stage refresh refused', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def register_cli(actions):
    parser = actions.add_parser('insights-refresh-stage', help='Build metadata into a NEW private stage; never publish')
    parser.add_argument('--stage-dir', required=True)
    parser.add_argument('--schema-sha256', required=True)
    parser.add_argument('--max-rows', type=int, default=10_000)
    parser.add_argument('--max-bytes', type=int, default=8 * 1024 * 1024)
    parser.add_argument('--seconds', type=float, default=5)
    parser.set_defaults(func=cmd_refresh_stage)
