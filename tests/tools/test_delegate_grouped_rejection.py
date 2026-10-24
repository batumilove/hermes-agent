"""Behavioral regressions for partial grouped admission and terminal bookkeeping."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tools import delegate_tool_dispatch as dd
from tools import delegation_live_log as live
from hermes_cli.observability import shared_metrics_loop as metrics


def batch(tmp_path):
    tasks = [{"goal": f"goal {i}"} for i in range(3)]
    did, writers, paths = live.create_live_transcripts(tasks, home=tmp_path)
    children = [(i, t, SimpleNamespace(_subagent_id=f"child-{i}", close=MagicMock())) for i, t in enumerate(tasks)]
    parent = SimpleNamespace(_active_children=[c for _, _, c in children], _active_children_lock=threading.Lock())
    return dd._Batch(tasks, children, parent, {"model": "m"}, None, "leaf", 3,
                     did, writers, paths, "", "", None, None, False, 0, live_home=tmp_path)


@pytest.mark.parametrize("field", ["handle", "goals", "note", "transcripts"])
def test_partial_payload_uses_only_accepted_children(tmp_path, field):
    b = batch(tmp_path)
    unit = dd.replace(b, children=[b.children[0], b.children[2]], group="survivor")
    p = dd._dispatched_payload(dd.replace(b, children=unit.children), [(unit, b.live_deleg_id + "-1")])
    if field == "handle":
        assert p["delegation_id"] == b.live_deleg_id + "-1"
    elif field == "goals":
        assert p["goals"] == ["goal 0", "goal 2"]
        assert p["count"] == len(p["subagent_ids"]) == 2
    elif field == "note":
        assert "2 subagents" in p["note"]
    else:
        assert p["live_transcripts"] == [str(b.live_writers[i].path) for i in (0, 2)]


@pytest.mark.parametrize("partial", [False, True])
def test_rejected_metrics_close_once_with_accepted_completions(tmp_path, monkeypatch, partial):
    b = batch(tmp_path)
    emitted = []
    monkeypatch.setattr(metrics, "_emit", lambda *a, **kw: emitted.append((a, kw)))
    metrics.begin_delegation_run(b.task_list, subagents=3, depth=1)
    try:
        rejected = dd.replace(b, children=b.children[1:]) if partial else b
        dd._reject_unowned_children(rejected, {"error": "unavailable"})
        if partial:
            assert not emitted
            metrics.finish_delegation_unit(b.task_list, [{"status": "completed"}], background=True)
        assert id(b.task_list) not in metrics._DELEGATIONS
        assert len(emitted) == 1
        assert emitted[0][1]["statuses"] == (["rejected", "rejected", "completed"] if partial else ["rejected"] * 3)
    finally:
        metrics._DELEGATIONS.pop(id(b.task_list), None)


def test_partial_manifest_is_not_completed(tmp_path):
    b = batch(tmp_path)
    dd._reject_unowned_children(dd.replace(b, children=b.children[1:]), {"error": "unavailable"})
    manifest = json.loads(live._manifest_path(b.live_deleg_id, tmp_path).read_text())
    assert [t["status"] for t in manifest["tasks"]] == ["running", "rejected", "rejected"]
    assert "completed" not in manifest


def test_concurrent_manifest_updates_preserve_both_terminal_results(tmp_path, monkeypatch):
    b = batch(tmp_path)
    original = live._dump_json
    first_write = threading.Event()
    second_done = threading.Event()
    errors = []
    def dump(path, data):
        if threading.current_thread().name == "first-update":
            first_write.set()
            second_done.wait(.3)
        return original(path, data)
    monkeypatch.setattr(live, "_dump_json", dump)
    def update(index, status):
        try:
            live.update_manifest_statuses(b.live_deleg_id, [{"task_index": index, "status": status}], home=tmp_path)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if index == 1:
                second_done.set()
    first = threading.Thread(target=update, args=(0, "completed"), name="first-update")
    second = threading.Thread(target=update, args=(1, "rejected"))
    first.start()
    assert first_write.wait(2)
    second.start()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive() and not errors
    data = json.loads(live._manifest_path(b.live_deleg_id, tmp_path).read_text())
    assert [t["status"] for t in data["tasks"]] == ["completed", "rejected", "running"]
