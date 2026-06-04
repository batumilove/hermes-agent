from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest import mock


PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "rtk-rewrite" / "__init__.py"


class FakeContext:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, hook_name, callback):
        self.hooks[hook_name] = callback


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def load_plugin_module(module_name="rtk_rewrite_plugin_under_test"):
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registered_callback():
    module = load_plugin_module()
    module._rtk_available = None
    module._rtk_missing_warned = False
    ctx = FakeContext()
    with mock.patch.object(module.shutil, "which", return_value="/usr/bin/rtk"):
        module.register(ctx)
    assert "pre_tool_call" in ctx.hooks
    return module, ctx.hooks["pre_tool_call"]


def test_missing_rtk_skips_hook_registration_and_warns_once(capsys):
    module = load_plugin_module()
    module._rtk_available = None
    module._rtk_missing_warned = False
    ctx = FakeContext()

    with mock.patch.object(module.shutil, "which", return_value=None):
        module.register(ctx)
        module.register(ctx)

    assert "pre_tool_call" not in ctx.hooks
    stderr = capsys.readouterr().err
    assert stderr.count("rtk binary not found in PATH") == 1


def test_terminal_command_is_rewritten_in_place():
    module, callback = registered_callback()
    args = {"command": "git status"}

    with mock.patch.object(
        module.subprocess,
        "run",
        return_value=FakeCompletedProcess(returncode=0, stdout="rtk git status\n"),
    ) as run:
        callback(tool_name="terminal", args=args)

    assert args == {"command": "rtk git status"}
    assert module._METRICS["attempts"] == 1
    assert module._METRICS["rewrites"] == 1
    assert module._METRICS["events_by_command"][("git", "rewrite")] == 1
    run.assert_called_once_with(
        ["rtk", "rewrite", "git status"],
        shell=False,
        timeout=2,
        capture_output=True,
        text=True,
    )


def test_non_terminal_and_invalid_payloads_are_noops():
    module, callback = registered_callback()

    with mock.patch.object(module.subprocess, "run") as run:
        callback(tool_name="read_file", args={"path": "README.md"})
        callback(tool_name="terminal", args={})
        callback(tool_name="terminal", args={"command": 123})
        callback(tool_name="terminal", args=None)

    run.assert_not_called()


def test_passthrough_return_codes_keep_original_command_quietly(capsys):
    module, callback = registered_callback()
    args = {"command": "echo hello"}

    with mock.patch.object(
        module.subprocess,
        "run",
        return_value=FakeCompletedProcess(returncode=1, stdout=""),
    ):
        callback(tool_name="terminal", args=args)

    assert args == {"command": "echo hello"}
    assert module._METRICS["attempts"] == 1
    assert module._METRICS["passthrough"] == 1
    assert module._METRICS["events_by_command"][("echo", "passthrough")] == 1
    assert capsys.readouterr().err == ""


def test_unexpected_rtk_failure_warns_but_keeps_original_command(capsys):
    module, callback = registered_callback()
    args = {"command": "git status"}

    with mock.patch.object(
        module.subprocess,
        "run",
        return_value=FakeCompletedProcess(returncode=42, stderr="boom"),
    ):
        callback(tool_name="terminal", args=args)

    assert args == {"command": "git status"}
    assert module._METRICS["attempts"] == 1
    assert module._METRICS["failures"] == 1
    assert module._METRICS["events_by_command"][("git", "failure")] == 1
    assert "rtk rewrite failed with exit 42: boom" in capsys.readouterr().err


def test_timeout_fails_open_and_warns(capsys):
    module, callback = registered_callback()
    args = {"command": "git status"}

    with mock.patch.object(
        module.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd=["rtk"], timeout=2),
    ):
        callback(tool_name="terminal", args=args)

    assert args == {"command": "git status"}
    assert module._METRICS["attempts"] == 1
    assert module._METRICS["timeouts"] == 1
    assert module._METRICS["events_by_command"][("git", "timeout")] == 1
    assert "rtk rewrite timed out" in capsys.readouterr().err


def test_metrics_are_written_to_prometheus_textfile(tmp_path, monkeypatch):
    module, callback = registered_callback()
    monkeypatch.setenv("HERMES_RTK_METRICS_FILE", str(tmp_path / "rtk.prom"))

    with mock.patch.object(
        module.subprocess,
        "run",
        return_value=FakeCompletedProcess(returncode=0, stdout="rtk git status\n"),
    ):
        callback(tool_name="terminal", args={"command": "git status"})

    metrics = (tmp_path / "rtk.prom").read_text()
    assert "hermes_rtk_rewrite_attempts_total 1" in metrics
    assert "hermes_rtk_rewrites_total 1" in metrics
    assert 'hermes_rtk_rewrite_events_total{command="git",event="rewrite"} 1' in metrics
    assert "hermes_rtk_binary_available 1" in metrics


def test_metrics_override_rejects_non_prometheus_textfile_path(tmp_path, monkeypatch, capsys):
    module = load_plugin_module("rtk_rewrite_plugin_bad_metrics_path")
    monkeypatch.setenv("HERMES_RTK_METRICS_FILE", str(tmp_path / "authorized_keys"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    path = module._metrics_file()

    assert path.name == "rtk_rewrite.prom"
    assert "ignoring HERMES_RTK_METRICS_FILE override" in capsys.readouterr().err


def test_manifest_exists_and_declares_pre_tool_hook():
    manifest = PLUGIN_PATH.with_name("plugin.yaml").read_text()

    assert "name: rtk-rewrite" in manifest
    assert "pre_tool_call" in manifest
