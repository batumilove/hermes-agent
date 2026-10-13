"""Behavioral contracts split from the main SessionDB test suite."""

import pytest


class TestFts5SanitizerCharacterClass:
    """Every character FTS5 rejects outside a quoted phrase must be stripped.

    A survivor reaches MATCH raw and raises, which the execute site swallows
    into zero results — so the search silently finds nothing rather than
    erroring. Assertions run the sanitized text against a real FTS5 table.
    """

    @staticmethod
    def _fts_table():
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
        conn.execute(
            "INSERT INTO t (content) VALUES "
            "('meet me at user host about gateway run py it s 50 a b')"
        )
        return conn

    @staticmethod
    def _sanitize(query):
        from hermes_state_search import SessionSearchMixin

        return SessionSearchMixin._sanitize_fts5_query(query)

    @pytest.mark.parametrize(
        "query",
        [
            "it's",                 # apostrophe — ordinary prose
            "gateway/run.py",       # path separator
            "user@host",            # email / handle
            "a,b",                  # comma
            "why?",                 # question mark
            "e=mc2",                # equals
            "a;b", "a!b", "a&b", "a|b", "x~y",
            "#tag", "$dollar", "[bracket]", "<tag>",
            r"C:\path\file",        # backslash
        ],
    )
    def test_query_stays_parsable(self, query):
        conn = self._fts_table()
        sanitized = self._sanitize(query)
        if not sanitized.strip():
            return
        # Raises sqlite3.OperationalError if a special character survived.
        conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (sanitized,)).fetchone()

    def test_plain_terms_are_untouched(self):
        assert self._sanitize("hello world").split() == ["hello", "world"]

    def test_quoted_phrase_survives(self):
        assert '"exact phrase"' in self._sanitize('"exact phrase"')

    def test_hyphen_dotted_term_still_quoted(self):
        # Step 5's behaviour must not regress: my-app.config.ts stays one term.
        assert '"my-app.config.ts"' in self._sanitize("my-app.config.ts")

    def test_prefix_star_still_works(self):
        conn = self._fts_table()
        sanitized = self._sanitize("gate*")
        rows = conn.execute(
            "SELECT count(*) FROM t WHERE t MATCH ?", (sanitized,)
        ).fetchone()
        assert rows[0] == 1

    def test_percent_stripped_for_non_cjk_query(self):
        # % is kept only for the CJK LIKE fallback; a non-CJK query never
        # reaches that fallback, so % must be stripped before MATCH.
        conn = self._fts_table()
        sanitized = self._sanitize("50%")
        assert "%" not in sanitized
        conn.execute(
            "SELECT count(*) FROM t WHERE t MATCH ?", (sanitized,)
        ).fetchone()

    def test_percent_preserved_for_cjk_query(self):
        # The CJK LIKE fallback builds its own pattern from the sanitized
        # text; keep % intact there (pre-existing contract).
        sanitized = self._sanitize("完成50%")
        assert "%" in sanitized
