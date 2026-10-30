"""Controls for the live-refusal oracle, using real SQLite and ownership."""
import sqlite3

import pytest

from gateway.runtime_ownership import ProfileOwnership
from hermes_cli.backup import restore_quick_snapshot
from tests.hermes_cli.test_runtime_maintenance_exclusion import (
    _live_bundle_state, _assert_live_bundle_unchanged,
)


@pytest.fixture
def live_database(tmp_path):
    home = tmp_path / 'state'
    home.mkdir()
    path = home / 'state.db'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE marker(value)')
    writer.commit()
    writer.execute('INSERT INTO marker VALUES(42)')
    writer.commit()
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        yield home, path, writer
    finally:
        owner.close()
        writer.close()


def test_readonly_bookkeeping_is_not_a_refused_restore_mutation(live_database, caplog):
    home, path, writer = live_database
    # Do the logical read before advancing WAL, leaving an older read mark.
    _live_bundle_state(path)
    writer.execute('INSERT INTO marker VALUES(43)')
    expected_rows = tuple(writer.iterdump())
    writer.commit()
    # Do not read SQLite between this physical baseline and the reader below:
    # even writer.iterdump() after COMMIT would consume the new read mark.
    before = _live_bundle_state(path, logical=False)
    shm_before = path.with_name('state.db-shm').read_bytes()
    assert not restore_quick_snapshot('not-opened-under-live-authority', hermes_home=home)
    assert 'maintenance refused' in caplog.text.lower()
    reader = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        assert reader.execute('SELECT * FROM marker').fetchall() == [(42,), (43,)]
        assert path.with_name('state.db-shm').read_bytes() != shm_before
        _assert_live_bundle_unchanged(before, _live_bundle_state(path, logical=False))
        assert _live_bundle_state(path)['logical'] == expected_rows
    finally:
        reader.close()


@pytest.mark.parametrize('suffix', ['', '-wal'])
def test_oracle_rejects_durable_file_mutation(live_database, suffix):
    home, path, _ = live_database
    before = _live_bundle_state(path)
    assert not restore_quick_snapshot('not-opened-under-live-authority', hermes_home=home)
    target = path.with_name(path.name + suffix)
    original = target.read_bytes()
    try:
        with target.open('r+b') as stream:
            stream.seek(len(original) - 1)
            stream.write(bytes([original[-1] ^ 1]))
        with pytest.raises(AssertionError, match='durable'):
            _assert_live_bundle_unchanged(before, _live_bundle_state(path, logical=False))
    finally:
        target.write_bytes(original)


def test_oracle_rejects_logical_mutation_independently(live_database):
    home, path, writer = live_database
    before = _live_bundle_state(path)
    assert not restore_quick_snapshot('not-opened-under-live-authority', hermes_home=home)
    writer.execute('UPDATE marker SET value=99')
    writer.commit()
    after = _live_bundle_state(path)
    # Hold physical inputs equal to isolate the logical clause; the ordinary
    # oracle also rejects this real commit through its changed WAL hash.
    physical_after_with_old_rows = dict(after, logical=before['logical'])
    with pytest.raises(AssertionError, match='logical'):
        _assert_live_bundle_unchanged(physical_after_with_old_rows, after)
