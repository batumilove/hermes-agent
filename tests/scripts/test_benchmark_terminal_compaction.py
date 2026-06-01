from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_terminal_compaction.py"


def load_benchmark_module(module_name="benchmark_terminal_compaction_under_test"):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_estimate_tokens_uses_rough_four_chars_per_token_flooring_up():
    module = load_benchmark_module()

    assert module.estimate_tokens("") == 0
    assert module.estimate_tokens("abcd") == 1
    assert module.estimate_tokens("abcde") == 2


def test_compare_outputs_preserves_exit_code_and_computes_savings():
    module = load_benchmark_module()

    row = module.compare_outputs(
        command="git status",
        raw_output="a" * 100,
        rtk_output="b" * 25,
        raw_returncode=1,
        rtk_returncode=1,
        raw_seconds=0.10,
        rtk_seconds=0.12,
    )

    assert row["command"] == "git status"
    assert row["raw_chars"] == 100
    assert row["rtk_chars"] == 25
    assert row["savings_ratio"] == 0.75
    assert row["exit_code_preserved"] is True
    assert row["overhead_ms"] == 20.0


def test_rewrite_command_uses_rtk_rewrite_and_falls_back_to_original(monkeypatch):
    module = load_benchmark_module()

    def fake_run_success(argv, **kwargs):
        assert argv == ["rtk", "rewrite", "git status"]
        return module.Completed(returncode=0, stdout="rtk git status\n", stderr="", seconds=0.01)

    monkeypatch.setattr(module, "run_command", fake_run_success)
    assert module.rewrite_command("git status", "rtk") == "rtk git status"

    def fake_run_passthrough(argv, **kwargs):
        return module.Completed(returncode=1, stdout="", stderr="", seconds=0.01)

    monkeypatch.setattr(module, "run_command", fake_run_passthrough)
    assert module.rewrite_command("echo hello", "rtk") == "echo hello"
