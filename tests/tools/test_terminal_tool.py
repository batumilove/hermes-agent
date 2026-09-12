"""Regression tests for sudo detection and sudo password handling."""

import tools.terminal_tool as terminal_tool


def setup_function():
    terminal_tool._reset_cached_sudo_passwords()


def teardown_function():
    terminal_tool._reset_cached_sudo_passwords()


def test_searching_for_sudo_does_not_trigger_rewrite(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    command = "rg --line-number --no-heading --with-filename 'sudo' . | head -n 20"
    transformed, sudo_stdin = terminal_tool._transform_sudo_command(command)

    assert transformed == command
    assert sudo_stdin is None


def test_terminal_schema_advertises_persistent_env_state():
    description = terminal_tool.TERMINAL_TOOL_DESCRIPTION

    assert "exported environment variables persist between calls" in description
    assert "activate a virtualenv" in description
    assert "once per session" in description


def test_printf_literal_sudo_does_not_trigger_rewrite(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    command = "printf '%s\\n' sudo"
    transformed, sudo_stdin = terminal_tool._transform_sudo_command(command)

    assert transformed == command
    assert sudo_stdin is None


def test_non_command_argument_named_sudo_does_not_trigger_rewrite(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    command = "grep -n sudo README.md"
    transformed, sudo_stdin = terminal_tool._transform_sudo_command(command)

    assert transformed == command
    assert sudo_stdin is None


def test_actual_sudo_command_uses_configured_password(monkeypatch):
    monkeypatch.setenv("SUDO_PASSWORD", "testpass")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    transformed, sudo_stdin = terminal_tool._transform_sudo_command("sudo apt install -y ripgrep")

    assert transformed == "sudo -S -p '' apt install -y ripgrep"
    assert sudo_stdin == "testpass\n"


def test_explicit_empty_sudo_password_tries_empty_without_prompt(monkeypatch):
    monkeypatch.setenv("SUDO_PASSWORD", "")
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")

    def _fail_prompt(*_args, **_kwargs):
        raise AssertionError("interactive sudo prompt should not run for explicit empty password")

    monkeypatch.setattr(terminal_tool, "_prompt_for_sudo_password", _fail_prompt)

    transformed, sudo_stdin = terminal_tool._transform_sudo_command("sudo true")

    assert transformed == "sudo -S -p '' true"
    assert sudo_stdin == "\n"


def test_validate_workdir_blocks_shell_metacharacters_in_windows_paths():
    assert terminal_tool._validate_workdir(r"C:\Users\Alice\project; rm -rf /")
    assert terminal_tool._validate_workdir(r"C:\Users\Alice\project$(whoami)")
    assert terminal_tool._validate_workdir("C:\\Users\\Alice\\project\nwhoami")


def test_validate_workdir_allows_unicode_filesystem_paths():
    assert terminal_tool._validate_workdir(
        "/Users/alice/Documents/Obs_Hermes_Data/项目-projects/客户拜访"
    ) is None
    assert terminal_tool._validate_workdir("/tmp/テスト") is None
    assert terminal_tool._validate_workdir("/home/jürgen/über projekt") is None


def test_validate_workdir_still_blocks_metachars_in_unicode_paths():
    # Widening to Unicode letters must not open the injection boundary:
    # shell metacharacters and control chars stay rejected even when mixed
    # with non-ASCII path segments.
    assert terminal_tool._validate_workdir("/tmp/テスト; rm -rf /")
    assert terminal_tool._validate_workdir("/tmp/项目$(whoami)")
    assert terminal_tool._validate_workdir("/tmp/über`id`")
    assert terminal_tool._validate_workdir("/tmp/テスト\nwhoami")
    assert terminal_tool._validate_workdir("/tmp/项目|cat /etc/passwd")
    assert terminal_tool._validate_workdir("/tmp/ü\x00ber")


def test_count_real_sudo_invocations_ignores_mentions(monkeypatch):
    assert terminal_tool._count_real_sudo_invocations("grep sudo README.md") == 0
    assert terminal_tool._count_real_sudo_invocations("sudo a; sudo b") == 2


def test_live_state_db_guard_cannot_be_bypassed_with_force(monkeypatch):
    monkeypatch.setattr(
        terminal_tool,
        "_check_live_state_db_guard",
        lambda **_kwargs: (True, "test live state.db block"),
        raising=False,
    )

    result = terminal_tool.json.loads(
        terminal_tool.terminal_tool("printf should-not-run", force=True)
    )

    assert result["status"] == "blocked"
    assert result["exit_code"] == 1
    assert result["output"] == ""
    assert result["error"] == "Blocked: test live state.db block."


def test_live_state_db_guard_bridge_forwards_host_access(monkeypatch):
    from tools import live_state_db_guard

    seen = {}

    def _check(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return False, None

    monkeypatch.setattr(live_state_db_guard, "check_live_state_db_command", _check)

    assert terminal_tool._check_live_state_db_guard(
        command="sqlite3 /host/state.db",
        env_type="docker",
        cwd="/work",
        has_host_access=True,
        target_aliases=("/db/state.db",),
    ) == (False, None)
    assert seen == {
        "command": "sqlite3 /host/state.db",
        "env_type": "docker",
        "cwd": "/work",
        "has_host_access": True,
        "target_aliases": ("/db/state.db",),
    }


def test_docker_live_state_db_aliases_translate_configured_bind_mount(tmp_path):
    profile_home = tmp_path / "profile"
    target = profile_home / "state.db"
    config = {
        "env_type": "docker",
        "docker_volumes": [f"{profile_home}:/db:ro"],
    }

    assert terminal_tool._docker_live_state_db_aliases(
        config, target=target, task_id="default"
    ) == ("/db/state.db",)


def test_docker_live_state_db_aliases_translate_automatic_workspace_mount(tmp_path):
    target = tmp_path / ".hermes" / "state.db"
    config = {
        "env_type": "docker",
        "host_cwd": str(tmp_path),
        "docker_mount_cwd_to_workspace": True,
    }

    assert terminal_tool._docker_live_state_db_aliases(
        config, target=target, task_id="default"
    ) == ("/workspace/.hermes/state.db",)
