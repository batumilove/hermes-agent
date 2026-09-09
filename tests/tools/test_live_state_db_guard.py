"""Fail-closed regression tests for terminal access to a live state.db."""

import json
import os
from pathlib import Path

from tools.live_state_db_guard import check_live_state_db_command, gateway_is_live


def _check(command: str, tmp_path: Path, *, live: bool = True):
    home = tmp_path / ".hermes"
    home.mkdir(exist_ok=True)
    return check_live_state_db_command(
        command,
        env_type="local",
        cwd=tmp_path,
        hermes_home=home,
        gateway_is_live=lambda _home: live,
    )


def test_blocks_sqlite_cli_against_absolute_live_state_db(tmp_path):
    db = tmp_path / ".hermes" / "state.db"

    blocked, reason = _check(f"sqlite3 {db} 'select 1'", tmp_path)

    assert blocked is True
    assert "live state.db" in reason
    assert "hermes state-db query" in reason


def test_blocks_sqlite_cli_with_hermes_home_expansion(tmp_path):
    blocked, _ = _check("sqlite3 $HERMES_HOME/state.db '.tables'", tmp_path)

    assert blocked is True


def test_blocks_sqlite_cli_with_trimmed_hermes_home_expansion(tmp_path):
    blocked, _ = _check("sqlite3 ${HERMES_HOME%/}/state.db '.tables'", tmp_path)

    assert blocked is True


def test_blocks_sqlite_cli_through_shell_carrier(tmp_path):
    blocked, _ = _check(
        'bash -c "sqlite3 ~/.hermes/state.db \'pragma quick_check\'"',
        tmp_path,
    )

    assert blocked is True


def test_blocks_direct_system_python_connection_to_live_state_db(tmp_path):
    db = tmp_path / ".hermes" / "state.db"
    command = f'''/usr/bin/python3 -c "import sqlite3; sqlite3.connect('{db}')"'''

    blocked, _ = _check(command, tmp_path)

    assert blocked is True


def test_blocks_inline_python_that_constructs_active_profile_path(tmp_path):
    command = (
        "python3 -c \"import sqlite3; from hermes_constants import get_hermes_home; "
        "sqlite3.connect(get_hermes_home() / 'state.db')\""
    )

    blocked, _ = _check(command, tmp_path)

    assert blocked is True


def test_allows_benign_inline_python_that_only_mentions_target(tmp_path):
    command = "python3 -c \"print('get_hermes_home state.db')\""

    blocked, reason = _check(command, tmp_path)

    assert blocked is False
    assert reason is None


def test_blocks_pypy_connection_to_live_state_db(tmp_path):
    db = tmp_path / ".hermes" / "state.db"
    command = f'''pypy3 -c "import sqlite3; sqlite3.connect('{db}')"'''

    blocked, _ = _check(command, tmp_path)

    assert blocked is True


def test_blocks_python_script_that_references_live_state_db(tmp_path):
    script = tmp_path / "inspect_db.py"
    script.write_text(
        "import sqlite3\n"
        "sqlite3.connect('" + str(tmp_path / ".hermes" / "state.db") + "')\n",
        encoding="utf-8",
    )

    blocked, _ = _check("python3 inspect_db.py", tmp_path)

    assert blocked is True


def test_blocks_python_script_that_constructs_active_profile_path(tmp_path):
    script = tmp_path / "inspect_db.py"
    script.write_text(
        "import sqlite3\n"
        "from hermes_constants import get_hermes_home\n"
        "sqlite3.connect(get_hermes_home() / 'state.db')\n",
        encoding="utf-8",
    )

    blocked, _ = _check("python3 inspect_db.py", tmp_path)

    assert blocked is True


def test_blocks_extensionless_python_script(tmp_path):
    script = tmp_path / "inspect_db"
    script.write_text(
        "import sqlite3\n"
        "from hermes_constants import get_hermes_home\n"
        "sqlite3.connect(get_hermes_home() / 'state.db')\n",
        encoding="utf-8",
    )

    blocked, _ = _check("python3 inspect_db", tmp_path)

    assert blocked is True


def test_blocks_direct_python_shebang_script(tmp_path):
    script = tmp_path / "inspect_db"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sqlite3\n"
        "from hermes_constants import get_hermes_home\n"
        "sqlite3.connect(get_hermes_home() / 'state.db')\n",
        encoding="utf-8",
    )

    blocked, _ = _check("./inspect_db", tmp_path)

    assert blocked is True


def test_blocks_local_python_module(tmp_path):
    package = tmp_path / "dbprobe"
    package.mkdir()
    (package / "__main__.py").write_text(
        "import sqlite3\n"
        "from hermes_constants import get_hermes_home\n"
        "sqlite3.connect(get_hermes_home() / 'state.db')\n",
        encoding="utf-8",
    )

    blocked, _ = _check("python3 -m dbprobe", tmp_path)

    assert blocked is True


def test_allows_python_script_that_only_mentions_sqlite_and_target(tmp_path):
    script = tmp_path / "explain_db.py"
    script.write_text(
        "print('sqlite3 docs for " + str(tmp_path / ".hermes" / "state.db") + "')\n",
        encoding="utf-8",
    )

    blocked, reason = _check("python3 explain_db.py", tmp_path)

    assert blocked is False
    assert reason is None


def test_blocks_shell_script_that_launches_sqlite_cli(tmp_path):
    script = tmp_path / "inspect_db.sh"
    script.write_text(
        "#!/bin/sh\nsqlite3 $HERMES_HOME/state.db 'select 1'\n",
        encoding="utf-8",
    )

    blocked, _ = _check("bash inspect_db.sh", tmp_path)

    assert blocked is True


def test_blocks_shell_script_that_launches_python_sqlite(tmp_path):
    script = tmp_path / "inspect_db.sh"
    script.write_text(
        "#!/bin/sh\n"
        "python3 -c \"import sqlite3; from hermes_constants import get_hermes_home; "
        "sqlite3.connect(get_hermes_home() / 'state.db')\"\n",
        encoding="utf-8",
    )

    blocked, _ = _check("bash inspect_db.sh", tmp_path)

    assert blocked is True


def test_blocks_shell_script_that_launches_python_file(tmp_path):
    shell_script = tmp_path / "inspect_db.sh"
    python_script = tmp_path / "inspect_db.py"
    shell_script.write_text("#!/bin/sh\npython3 inspect_db.py\n", encoding="utf-8")
    python_script.write_text(
        "import sqlite3\n"
        "from hermes_constants import get_hermes_home\n"
        "sqlite3.connect(get_hermes_home() / 'state.db')\n",
        encoding="utf-8",
    )

    blocked, _ = _check("bash inspect_db.sh", tmp_path)

    assert blocked is True


def test_allows_unrelated_sqlite_database(tmp_path):
    blocked, reason = _check("sqlite3 ./fixture.db 'select 1'", tmp_path)

    assert blocked is False
    assert reason is None


def test_allows_state_db_access_when_gateway_is_down(tmp_path):
    db = tmp_path / ".hermes" / "state.db"

    blocked, reason = _check(f"sqlite3 {db} 'select 1'", tmp_path, live=False)

    assert blocked is False
    assert reason is None


def test_allows_isolated_backend_without_host_access(tmp_path):
    home = tmp_path / ".hermes"
    blocked, reason = check_live_state_db_command(
        "sqlite3 ~/.hermes/state.db 'select 1'",
        env_type="docker",
        cwd=tmp_path,
        hermes_home=home,
        gateway_is_live=lambda _home: True,
    )

    assert blocked is False
    assert reason is None


def test_blocks_host_mounted_docker_backend(tmp_path):
    home = tmp_path / ".hermes"
    blocked, _ = check_live_state_db_command(
        "sqlite3 ~/.hermes/state.db 'select 1'",
        env_type="docker",
        has_host_access=True,
        cwd=tmp_path,
        hermes_home=home,
        gateway_is_live=lambda _home: True,
    )

    assert blocked is True


def test_blocks_container_alias_for_mounted_live_state_db(tmp_path):
    home = tmp_path / ".hermes"
    blocked, _ = check_live_state_db_command(
        "sqlite3 /db/state.db 'select 1'",
        env_type="docker",
        has_host_access=True,
        target_aliases=("/db/state.db",),
        cwd="/workspace",
        hermes_home=home,
        gateway_is_live=lambda _home: True,
    )

    assert blocked is True


def test_allows_canonical_read_only_helper_while_gateway_is_live(tmp_path):
    blocked, reason = _check(
        "hermes state-db query --sql 'select count(*) from messages'",
        tmp_path,
    )

    assert blocked is False
    assert reason is None


def test_blocks_even_when_path_is_file_uri(tmp_path):
    db = tmp_path / ".hermes" / "state.db"

    blocked, _ = _check(
        f"sqlite3 'file:{db}?mode=ro' 'select 1'",
        tmp_path,
    )

    assert blocked is True


def test_gateway_is_live_uses_shared_liveness_ladder(tmp_path, monkeypatch):
    from gateway import status

    home = tmp_path / ".hermes"
    home.mkdir()
    seen = {}

    def _resolve(**kwargs):
        seen.update(kwargs)
        return status.GatewayLiveness(running=True, pid=123, source="pid")

    monkeypatch.setattr(status, "resolve_gateway_liveness", _resolve)

    assert gateway_is_live(home) is True
    assert seen == {"profile_dir": home, "use_cache": False}


def test_gateway_liveness_probe_error_fails_closed(tmp_path, monkeypatch):
    from gateway import status

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(
        status,
        "resolve_gateway_liveness",
        lambda **_kwargs: status.GatewayLiveness(
            running=False,
            pid=None,
            source="none",
            probe_error=True,
        ),
    )

    assert gateway_is_live(home) is True


def test_gateway_process_fails_closed_when_state_file_is_unreadable(tmp_path, monkeypatch):
    from tools import process_registry

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)

    assert gateway_is_live(home) is True


def test_terminal_tool_blocks_actual_live_target_even_with_force(tmp_path, monkeypatch):
    import tools.terminal_tool as terminal_tool
    from tools import process_registry

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "state.db").touch()
    stat_text = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    start_ticks = int(stat_text[stat_text.rfind(")") + 2 :].split()[19])
    (home / "gateway_state.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "start_time": start_ticks,
                "kind": "hermes-gateway",
                "hermes_home": str(home),
                "gateway_state": "running",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)

    result = json.loads(
        terminal_tool.terminal_tool(
            f'''/usr/bin/python3 -c "import sqlite3; sqlite3.connect('{home / 'state.db'}')"''',
            force=True,
        )
    )

    assert result["status"] == "blocked"
    assert result["exit_code"] == 1
    assert "hermes state-db query" in result["error"]
