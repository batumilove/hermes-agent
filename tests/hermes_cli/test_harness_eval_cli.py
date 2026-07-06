from __future__ import annotations

import argparse
import json
from pathlib import Path

from hermes_cli.harness_eval import cmd_harness
from hermes_cli.subcommands.harness import build_harness_parser


def test_harness_eval_cli_emits_json_report(tmp_path: Path, capsys):
    case_file = tmp_path / "cases.jsonl"
    case_file.write_text(
        json.dumps(
            {
                "id": "ok",
                "failure_type": "reasoning",
                "prompt": "p",
                "expected_behavior": ["answer"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rc = cmd_harness(argparse.Namespace(harness_command="eval", paths=[str(case_file)], dry_run=True, json=True, trace=None))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
    assert out["valid"] == 1
    assert "trace_scoring" not in out


def test_harness_eval_cli_returns_nonzero_for_invalid_cases(tmp_path: Path, capsys):
    case_file = tmp_path / "bad.jsonl"
    case_file.write_text(
        json.dumps({"id": "bad", "failure_type": "reasoning", "prompt": "p"}) + "\n",
        encoding="utf-8",
    )

    rc = cmd_harness(argparse.Namespace(harness_command="eval", paths=[str(case_file)], dry_run=True, json=False, trace=None))

    assert rc == 1
    out = capsys.readouterr().out
    assert "invalid: 1" in out
    assert "expected_behavior or forbidden_behavior" in out


def test_harness_eval_cli_scores_trace_json(tmp_path: Path, capsys):
    case_file = tmp_path / "cases.jsonl"
    case_file.write_text(
        json.dumps(
            {
                "id": "cron-stop-means-pause",
                "failure_type": "action_realization",
                "prompt": "p",
                "expected_behavior": ["pause/disable matching job"],
                "forbidden_behavior": ["cronjob remove"],
                "required_evidence": ["job_id"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text(
        json.dumps(
            {
                "case_id": "cron-stop-means-pause",
                "tool_calls": [{"name": "cronjob", "args": {"action": "pause", "job_id": "abc"}, "result": {"job_id": "abc"}}],
                "final_response": "Paused cron job abc.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rc = cmd_harness(argparse.Namespace(harness_command="eval", paths=[str(case_file)], dry_run=True, json=True, trace=str(trace_file)))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["trace_scoring"]["scored"] == 1
    assert out["trace_scoring"]["passed"] == 1


def test_harness_eval_cli_returns_nonzero_when_trace_scoring_fails(tmp_path: Path, capsys):
    case_file = tmp_path / "cases.jsonl"
    case_file.write_text(
        json.dumps(
            {
                "id": "cron-stop-means-pause",
                "failure_type": "action_realization",
                "prompt": "p",
                "expected_behavior": ["pause/disable matching job"],
                "forbidden_behavior": ["cronjob remove"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text(
        json.dumps(
            {
                "case_id": "cron-stop-means-pause",
                "tool_calls": [{"name": "cronjob", "args": {"action": "remove"}, "result": {"status": "removed"}}],
                "final_response": "Removed cron job.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rc = cmd_harness(argparse.Namespace(harness_command="eval", paths=[str(case_file)], dry_run=True, json=False, trace=str(trace_file)))

    assert rc == 1
    out = capsys.readouterr().out
    assert "trace scoring: 1 case(s), passed: 0, failed: 1" in out


def test_harness_parser_wires_eval_subcommand():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_harness_parser(subs, cmd_harness=cmd_harness)

    args = parser.parse_args(["harness", "eval", "cases.jsonl", "--trace", "trace.jsonl", "--dry-run", "--json"])

    assert args.command == "harness"
    assert args.harness_command == "eval"
    assert args.paths == ["cases.jsonl"]
    assert args.trace == "trace.jsonl"
    assert args.dry_run is True
    assert args.json is True
