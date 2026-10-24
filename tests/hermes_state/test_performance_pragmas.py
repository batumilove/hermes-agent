"""Behavioral contracts split from the main SessionDB test suite."""

import hermes_state_wal
from hermes_state import SessionDB


class TestPerformancePragmasEndToEnd:
    """E2E guard for PR #71755: config-gated cache_size / mmap_size /
    temp_store must reach EVERY connection type (writer, read-only
    cross-profile attach, WAL per-thread reader) — and default installs
    (no ``database:`` keys) must see byte-identical SQLite defaults.

    NOTE: SQLite's compiled-in default for ``cache_size`` is already
    ``-2000``, so the configured value here is ``-16000`` — a value the
    test can actually discriminate from the default (a reverted prod
    change must FAIL this test, not accidentally pass it).
    """

    PRAGMAS = ("cache_size", "mmap_size", "temp_store")
    CONFIGURED = {"cache_size": -16000, "mmap_size": 1048576, "temp_store": 2}

    @staticmethod
    def _read(conn):
        return {
            name: conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("cache_size", "mmap_size", "temp_store")
        }

    @staticmethod
    def _sqlite_defaults(tmp_path):
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "baseline.db"))
        try:
            return {
                name: conn.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("cache_size", "mmap_size", "temp_store")
            }
        finally:
            conn.close()

    def _fresh_home(self, tmp_path, monkeypatch, config_text=None):

        # Local venvs may bundle a WAL-reset-vulnerable SQLite (e.g. 3.46.0),
        # which would silently disable WAL and skip the per-thread reader
        # path. Force WAL eligibility so _get_read_conn is truly exercised
        # (established pattern used by the WAL tests above).
        monkeypatch.setattr(
            hermes_state_wal, "is_sqlite_wal_reset_vulnerable",
            lambda version_info=None: False,
        )
        home = tmp_path / "hermes_home"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        if config_text is not None:
            (home / "config.yaml").write_text(config_text)
        return home

    def test_configured_pragmas_reach_all_connection_types(
        self, tmp_path, monkeypatch
    ):

        home = self._fresh_home(
            tmp_path,
            monkeypatch,
            "database:\n"
            "  cache_size: -16000\n"
            "  temp_store: 2\n"
            "  mmap_size: 1048576\n",
        )
        db_path = home / "state.db"
        db = SessionDB(db_path=db_path)
        try:
            # Writer connection.
            assert self._read(db._conn) == self.CONFIGURED
            # WAL per-thread reader.
            rconn = db._get_read_conn()
            assert rconn is not None, "WAL reader expected on local filesystem"
            assert self._read(rconn) == self.CONFIGURED
        finally:
            db.close()

        # Read-only cross-profile attach.
        ro = SessionDB(db_path=db_path, read_only=True)
        try:
            assert self._read(ro._conn) == self.CONFIGURED
        finally:
            ro.close()

    def test_defaults_unchanged_without_config(self, tmp_path, monkeypatch):
        """No database: keys in config.yaml → SQLite defaults untouched."""

        defaults = self._sqlite_defaults(tmp_path)
        home = self._fresh_home(tmp_path, monkeypatch, config_text=None)
        db_path = home / "state.db"
        db = SessionDB(db_path=db_path)
        try:
            assert self._read(db._conn) == defaults
            rconn = db._get_read_conn()
            if rconn is not None:
                assert self._read(rconn) == defaults
        finally:
            db.close()

        ro = SessionDB(db_path=db_path, read_only=True)
        try:
            assert self._read(ro._conn) == defaults
        finally:
            ro.close()
