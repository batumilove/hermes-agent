"""Read-only, bounded exact Insights projection validation; no recovery writes."""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote

from hermes_cli.sqlite_safe_read import connect_tracked

SESSION_COLUMNS = (
    'id,source,user_id,model,parent_session_id,started_at,ended_at,end_reason,'
    'message_count,tool_call_count,input_tokens,output_tokens,cache_read_tokens,'
    'cache_write_tokens,reasoning_tokens,title,billing_provider,billing_mode,'
    'estimated_cost_usd,actual_cost_usd,cost_status,cost_source,api_call_count,'
    'cwd,git_branch,git_repo_root,session_key,chat_id,chat_type,thread_id,'
    'display_name,archived,rewind_count'
)
MESSAGE_FIELDS = (
    "id,session_id,role,tool_name,timestamp,token_count,finish_reason,"
    "coalesce(active,0),coalesce(compacted,0),length(coalesce(content,'')),"
    "length(coalesce(tool_calls,''))"
)


@contextlib.contextmanager
def read_scope(path, deadline):
    """Tracked lifetime with query-only policy and a single pinned SQLite view."""
    from hermes_cli.state_db_cmd import _deny_attach

    connection = connect_tracked(
        f"file:{quote(str(path), safe='/')}?mode=ro", tracking_path=path,
        uri=True, timeout=0.05,
    )
    try:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA cache_size=-1024')
        connection.set_authorizer(_deny_attach)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection.execute('BEGIN')
        # BEGIN alone is deferred: force the snapshot before yielding.
        connection.execute('SELECT rootpage FROM sqlite_master LIMIT 1').fetchone()
        yield connection
    finally:
        connection.close()


def validate_metadata(args):
    from hermes_constants import get_hermes_home

    if not (1 <= args.max_rows <= 100_000 and 1 <= args.max_bytes <= 64 * 1024 * 1024
            and 0 < args.seconds <= 30):
        raise ValueError('invalid validation budget')
    if not re.fullmatch('[0-9a-f]{64}', args.schema_sha256):
        raise ValueError('invalid schema pin')
    source = (Path(get_hermes_home()) / 'state.db').resolve(strict=True)
    target = Path(args.summary).expanduser().resolve(strict=True)
    if source == target or source.samefile(target):
        raise ValueError('summary must differ from source')
    deadline = time.monotonic() + args.seconds
    with read_scope(source, deadline) as a, read_scope(target, deadline) as b:
        schema = list(a.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY type,name"))
        version = a.execute('PRAGMA user_version').fetchone()[0]
        fingerprint = hashlib.sha256(json.dumps({'user_version': version, 'objects': schema}, separators=(',', ':'), default=str).encode()).hexdigest()
        if fingerprint != args.schema_sha256:
            raise ValueError('source schema pin mismatch')
        meta = dict(b.execute("SELECT key,value FROM summary_meta WHERE key IN ('source_schema_sha256','bootstrap_complete')"))
        if meta != {'source_schema_sha256': fingerprint, 'bootstrap_complete': '1'}:
            raise ValueError('summary provenance/readiness mismatch')
        counts = []
        total_rows = total_bytes = highwater = 0
        sentinel = object()
        queries = [
            ('SELECT ' + MESSAGE_FIELDS + ' FROM messages ORDER BY id',
             'SELECT * FROM summary_message_metadata ORDER BY id'),
            ('SELECT ' + SESSION_COLUMNS + ' FROM sessions ORDER BY id',
             'SELECT ' + SESSION_COLUMNS + ' FROM summary_sessions ORDER BY id'),
        ]
        for index, (left_sql, right_sql) in enumerate(queries):
            count = 0
            for left, right in itertools.zip_longest(a.execute(left_sql), b.execute(right_sql), fillvalue=sentinel):
                if time.monotonic() >= deadline:
                    raise ValueError('validation deadline')
                total_rows += 1
                if total_rows > args.max_rows:
                    raise ValueError('validation row budget')
                if left != right:
                    raise ValueError('metadata projection incomplete')
                total_bytes += len(json.dumps(left, ensure_ascii=True, default=str).encode())
                if total_bytes > args.max_bytes:
                    raise ValueError('validation byte budget')
                count += 1
                if index == 0:
                    highwater = left[0]
            counts.append(count)
        if time.monotonic() >= deadline:
            raise ValueError('validation deadline')
        return {'complete': True, 'schema_sha256': fingerprint, 'sessions': counts[1],
                'messages': counts[0], 'message_high_water': highwater,
                'projection_bytes': total_bytes, 'scope': 'read-only validation; not publication readiness'}


def cmd_validate(args):
    try:
        result = validate_metadata(args)
    except (OSError, ValueError, sqlite3.Error):
        # Never include source values or SQLite SQL diagnostics in receipts.
        print('Insights metadata validation refused', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def register_cli(actions):
    parser = actions.add_parser('insights-validate', help='Validate exact metadata projection without writes')
    parser.add_argument('--summary', required=True)
    parser.add_argument('--schema-sha256', required=True)
    parser.add_argument('--max-rows', type=int, default=10_000)
    parser.add_argument('--max-bytes', type=int, default=8 * 1024 * 1024)
    parser.add_argument('--seconds', type=float, default=5)
    parser.set_defaults(func=cmd_validate)
