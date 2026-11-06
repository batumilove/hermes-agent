import argparse
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import sqlite3

import pytest
from hermes_cli import state_db_cmd
from tests.hermes_cli.test_insights_metadata_read import databases


def refresh(databases, stage, **options):
    parser = argparse.ArgumentParser()
    state_db_cmd.register_cli(parser.add_subparsers())
    args = parser.parse_args(['state-db', 'insights-refresh-stage', '--stage-dir', str(stage), '--schema-sha256', databases[3]] + [part for k, v in options.items() for part in ('--'+k.replace('_','-'), str(v))])
    return args.func(args)


def valid_source(d):
    con = d[1]
    con.execute("update sessions set source='cli',started_at=1")
    for c in ('tool_call_count,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,reasoning_tokens,api_call_count,archived,rewind_count').split(','):
        con.execute('update sessions set '+c+'=0')
    con.commit()


def test_bootstrap_actual_consumer_schema_and_catchup(databases, tmp_path, capsys):
    valid_source(databases)
    first = tmp_path/'stage1'
    assert refresh(databases, first) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['complete'] and result['messages'] == 2
    db = first/'metadata-summary.db'
    assert b'PRIVATE' not in db.read_bytes() and b'SECRET' not in db.read_bytes()
    # Historical consumer snapshot is inert text; execute its unchanged bytes only
    # inside this disposable-database compatibility test.
    fixture = Path(__file__).parent/'fixtures/insights/consumer.py.txt'
    loader = SourceFileLoader('fixture_consumer', str(fixture))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    consumer = importlib.util.module_from_spec(spec); spec.loader.exec_module(consumer)
    assert consumer.summary_readiness(db)['ready']
    detail = consumer.load_session_detail(db, 's')
    assert detail['role_counts'] == {'assistant': 1, 'user': 1}
    assert len(detail['timeline']) == 2 and detail['skills'] == []
    assert detail['timeline'][1]['content_chars'] == 6
    con = sqlite3.connect(db)
    assert con.execute('select content_chars,tool_calls_chars from summary_message_metadata where id=9').fetchone() == (6,2)
    assert con.execute('select role,count from summary_session_roles order by role').fetchall() == [('assistant',1),('user',1)]
    con.close()
    source = databases[1]
    source.execute("update messages set content='changed old message' where id=1")
    source.execute('delete from messages where id=9')
    source.commit()
    second = tmp_path/'stage2'
    assert refresh(databases, second) == 0
    con = sqlite3.connect(second/'metadata-summary.db')
    assert con.execute('select id,content_chars from summary_message_metadata').fetchall() == [(1,19)]
    con.close()
    source.execute('delete from messages'); source.execute('delete from sessions'); source.commit()
    assert refresh(databases,tmp_path/'stage3') == 0
    con=sqlite3.connect(tmp_path/'stage3/metadata-summary.db')
    assert con.execute('select count(*) from summary_sessions').fetchone() == (0,)
    con.close()


@pytest.mark.parametrize('options',[{'max_rows':1},{'max_bytes':1},{'seconds':0},{'schema_sha256':'0'*64}])
def test_budget_or_schema_rollback(databases,tmp_path,capsys,options):
    valid_source(databases)
    stage=tmp_path/'failed'
    # schema override uses parsed argument last-value semantics.
    assert refresh(databases,stage,**options) == 1
    assert not stage.exists()
    assert not capsys.readouterr().out


@pytest.mark.platforms('linux')
@pytest.mark.parametrize('replace_ancestor', [False, True])
@pytest.mark.parametrize('refuse', [False, True])
def test_replaced_path_preserves_unrelated_files(databases, tmp_path, monkeypatch,
                                                 replace_ancestor, refuse):
    from contextlib import contextmanager
    from hermes_cli import insights_refresh_stage as module

    valid_source(databases)
    parent = tmp_path / 'parent'
    parent.mkdir()
    stage = parent / 'stage'
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    marker = foreign / 'metadata-summary.db'
    marker.write_bytes(b'KEEP DATABASE')
    unrelated = foreign / 'unrelated'
    unrelated.write_bytes(b'KEEP UNRELATED')
    original = module.read_scope

    @contextmanager
    def replace(source, deadline):
        victim = parent if replace_ancestor else stage
        victim.rename(tmp_path / 'detached')
        victim.symlink_to(foreign, target_is_directory=True)
        if replace_ancestor:
            (foreign / 'stage').symlink_to(foreign, target_is_directory=True)
        with original(source, deadline) as reader:
            yield reader

    monkeypatch.setattr(module, 'read_scope', replace)
    options = {'schema_sha256': '0' * 64} if refuse else {}
    assert refresh(databases, stage, **options) == 1
    assert marker.read_bytes() == b'KEEP DATABASE'
    assert unrelated.read_bytes() == b'KEEP UNRELATED'


@pytest.mark.platforms('linux')
def test_replacement_at_file_creation_cannot_redirect(databases, tmp_path, monkeypatch):
    from hermes_cli import insights_refresh_stage as module

    valid_source(databases)
    stage = tmp_path / 'stage'
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    marker = foreign / 'unrelated'
    marker.write_bytes(b'KEEP')
    original = module.os.open

    def replace(path, flags, mode=0o777, *, dir_fd=None):
        if path == 'metadata-summary.db':
            stage.rename(tmp_path / 'detached')
            stage.symlink_to(foreign, target_is_directory=True)
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, 'open', replace)
    assert refresh(databases, stage) == 1
    assert marker.read_bytes() == b'KEEP'
    assert not (foreign / 'metadata-summary.db').exists()


def test_existing_target_never_modified(databases,tmp_path):
    valid_source(databases)
    stage=tmp_path/'occupied';stage.mkdir()
    marker=stage/'metadata-summary.db'; marker.write_bytes(b'KEEP')
    digest=hashlib.sha256(marker.read_bytes()).hexdigest()
    assert refresh(databases,stage) == 1
    assert hashlib.sha256(marker.read_bytes()).hexdigest()==digest
