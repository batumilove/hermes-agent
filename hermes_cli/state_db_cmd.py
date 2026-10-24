"""Canonical read-only inspection of the active Hermes session database."""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

_DEFAULT_MAX_ROWS = 500
_MAX_ALLOWED_ROWS = 10_000


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _deny_attach(action: int, _arg1, _arg2, _database, _trigger) -> int:
    if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def query_state_db(
    sql: str,
    *,
    hermes_home: str | Path | None = None,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Execute one bounded statement against state.db in SQLite read-only mode."""
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql must be a non-empty string")
    if not isinstance(max_rows, int) or isinstance(max_rows, bool):
        raise ValueError("max_rows must be an integer")
    if max_rows < 1 or max_rows > _MAX_ALLOWED_ROWS:
        raise ValueError(f"max_rows must be between 1 and {_MAX_ALLOWED_ROWS}")

    if hermes_home is None:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home()).expanduser()
    else:
        home = Path(hermes_home).expanduser()
    db_path = (home / "state.db").resolve(strict=False)
    if not db_path.is_file():
        raise FileNotFoundError(f"state database not found: {db_path}")

    uri = f"file:{quote(str(db_path), safe='/')}?mode=ro"
    from hermes_cli.sqlite_safe_read import connect_tracked

    connection = connect_tracked(
        uri,
        tracking_path=db_path,
        uri=True,
        timeout=5.0,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.set_authorizer(_deny_attach)
        cursor = connection.execute(sql)
        columns = [item[0] for item in (cursor.description or [])]
        fetched = cursor.fetchmany(max_rows + 1)
        rows = [
            [_json_value(value) for value in row]
            for row in fetched[:max_rows]
        ]
        return {
            "database": str(db_path),
            "sqlite_version": sqlite3.sqlite_version,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(fetched) > max_rows,
        }
    finally:
        connection.close()


def cmd_state_db(args: argparse.Namespace) -> int:
    action = getattr(args, "state_db_action", None)
    if action != "query":
        args._state_db_parser.print_help()
        return 0
    try:
        result = query_state_db(args.sql, max_rows=args.max_rows)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"state-db query failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def register_cli(subparsers) -> None:
    parser = subparsers.add_parser(
        "state-db",
        help="Safely inspect the active Hermes state database",
        description=(
            "Run one bounded SQL statement against HERMES_HOME/state.db using "
            "the Hermes Python/SQLite runtime, URI mode=ro, and query_only=ON."
        ),
    )
    parser.set_defaults(func=cmd_state_db, _state_db_parser=parser)
    actions = parser.add_subparsers(dest="state_db_action")
    query = actions.add_parser("query", help="Execute one read-only SQL statement")
    query.add_argument("--sql", required=True, help="SQL statement to execute")
    query.add_argument(
        "--max-rows",
        type=int,
        default=_DEFAULT_MAX_ROWS,
        help=f"Maximum rows to return (default {_DEFAULT_MAX_ROWS}, max {_MAX_ALLOWED_ROWS})",
    )
