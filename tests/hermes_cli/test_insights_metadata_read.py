"""Bounded Insights metadata validation, only disposable SQLite fixtures."""
import argparse
import hashlib
import json
import sqlite3

import pytest

from hermes_cli import state_db_cmd


SESSION = ('id', 'source', 'user_id', 'model', 'parent_session_id', 'started_at',
           'ended_at', 'end_reason', 'message_count', 'tool_call_count', 'input_tokens',
           'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens',
           'title', 'billing_provider', 'billing_mode', 'estimated_cost_usd',
           'actual_cost_usd', 'cost_status', 'cost_source', 'api_call_count', 'cwd',
           'git_branch', 'git_repo_root', 'session_key', 'chat_id', 'chat_type',
           'thread_id', 'display_name', 'archived', 'rewind_count')
MESSAGE = 'id,session_id,role,tool_name,timestamp,token_count,finish_reason,active,compacted,content,tool_calls'


@pytest.fixture
def databases(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    source = sqlite3.connect(tmp_path / 'state.db')
    source.execute('pragma journal_mode=wal')
    source.execute('create table sessions (' + ','.join(SESSION) + ')')
    source.execute('create table messages (' + MESSAGE + ')')
    source.execute('insert into sessions (id,message_count) values (?,?)', ('s', 2))
    source.executemany('insert into messages values (?,?,?,?,?,?,?,?,?,?,?)', [
        (1, 's', 'user', None, 1, 2, None, None, None, 'PRIVATE', None),
        (9, 's', 'assistant', None, 2, 3, 'stop', 1, 0, 'SECRET', '[]')])
    source.commit()
    schema = list(source.execute("select type,name,tbl_name,sql from sqlite_master where type in ('table','index') order by type,name"))
    fingerprint = hashlib.sha256(json.dumps({'user_version': 0, 'objects': schema}, separators=(',', ':'), default=str).encode()).hexdigest()
    summary = tmp_path / 'summary.db'
    target = sqlite3.connect(summary)
    target.execute('create table summary_meta (key primary key,value)')
    target.executemany('insert into summary_meta values (?,?)', [('source_schema_sha256', fingerprint), ('bootstrap_complete', '1')])
    target.execute('create table summary_sessions as select ' + ','.join('null as '+c for c in SESSION) + ' where 0')
    target.executemany('insert into summary_sessions values (' + ','.join('?' for _ in SESSION) + ')', source.execute('select * from sessions'))
    target.execute('create table summary_message_metadata (id,session_id,role,tool_name,timestamp,token_count,finish_reason,active,compacted,content_length,tool_calls_length)')
    target.executemany('insert into summary_message_metadata values (?,?,?,?,?,?,?,?,?,?,?)', source.execute("select id,session_id,role,tool_name,timestamp,token_count,finish_reason,coalesce(active,0),coalesce(compacted,0),length(coalesce(content,'')),length(coalesce(tool_calls,'')) from messages"))
    target.commit()
    target.close()
    yield tmp_path, source, summary, fingerprint
    source.close()


def invoke(databases, **kwargs):
    _home, _, summary, fingerprint = databases
    parser = argparse.ArgumentParser()
    state_db_cmd.register_cli(parser.add_subparsers())
    args = parser.parse_args(['state-db', 'insights-validate', '--summary', str(summary), '--schema-sha256', fingerprint] + [v for k, value in kwargs.items() for v in ('--'+k.replace('_','-'), str(value))])
    return args.func(args)


def test_cli_exact_projection_no_content(databases, capsys):
    assert invoke(databases) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['complete'] is True
    assert result['sessions'] == 1 and result['messages'] == 2
    assert result['message_high_water'] == 9
    assert 'PRIVATE' not in output and 'SECRET' not in output


@pytest.mark.parametrize('mutation', [
    'delete from summary_message_metadata where id=1',
    'update summary_message_metadata set content_length=99 where id=1',
    "update summary_sessions set message_count=99",
    "update summary_meta set value='0' where key='bootstrap_complete'",
    "update summary_meta set value='wrong' where key='source_schema_sha256'",
    'insert into summary_message_metadata select * from summary_message_metadata where id=1',
])
def test_incomplete_or_unbound_summary_refused(databases, capsys, mutation):
    target = sqlite3.connect(databases[2])
    target.execute(mutation)
    target.commit()
    target.close()
    assert invoke(databases) == 1
    assert not capsys.readouterr().out


@pytest.mark.parametrize('budget,value', [('max_rows', 1), ('max_bytes', 1), ('seconds', 0)])
def test_budget_refuses_without_partial_success(databases, capsys, budget, value):
    assert invoke(databases, **{budget:value}) == 1
    assert not capsys.readouterr().out


def test_schema_change_refused(databases, capsys):
    databases[1].execute('alter table messages add column new_column')
    databases[1].commit()
    assert invoke(databases) == 1
    assert not capsys.readouterr().out


def test_consistent_source_view_and_tracked_lifetime(databases):
    import time
    from hermes_cli.insights_metadata_read import read_scope
    from hermes_cli.sqlite_safe_read import has_live_connection

    path = databases[0] / 'state.db'
    with read_scope(path, time.monotonic() + 5) as reader:
        assert has_live_connection(path)
        assert reader.execute('select count(*) from messages').fetchone() == (2,)
        databases[1].execute('delete from messages where id=1')
        databases[1].commit()
        assert reader.execute('select count(*) from messages').fetchone() == (2,)
        with pytest.raises(sqlite3.Error):
            reader.execute('delete from messages')
        with pytest.raises(sqlite3.Error):
            reader.execute("attach ':memory:' as extra")
    assert not has_live_connection(path)
    assert databases[1].execute('select count(*) from messages').fetchone() == (1,)


def test_source_and_target_unchanged(databases):
    before = databases[1].total_changes
    digest = hashlib.sha256(databases[2].read_bytes()).hexdigest()
    assert invoke(databases) == 0
    assert databases[1].total_changes == before
    assert hashlib.sha256(databases[2].read_bytes()).hexdigest() == digest
