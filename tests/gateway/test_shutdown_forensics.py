"""Tests for gateway.shutdown_forensics — fast snapshot + async diag spawn."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from gateway import shutdown_forensics as sf


# ---------------------------------------------------------------------------
# _signal_name
# ---------------------------------------------------------------------------

class TestSignalName:

    def test_unknown_int_returns_signal_num_token(self):
        # Pick an integer extremely unlikely to ever be a real signal alias
        assert sf._signal_name(9999) == "signal#9999"


# ---------------------------------------------------------------------------
# snapshot_shutdown_context
# ---------------------------------------------------------------------------

class TestSnapshotShutdownContext:

    def test_handles_none_signal(self):
        ctx = sf.snapshot_shutdown_context(None)
        assert ctx["signal"] == "UNKNOWN"
        assert ctx["signal_num"] is None

    def test_includes_timestamps(self):
        before = time.time()
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        after = time.time()
        assert before <= ctx["ts"] <= after
        assert isinstance(ctx["ts_monotonic"], float)


    def test_under_systemd_false_without_invocation_id_and_normal_ppid(
        self, monkeypatch
    ):
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        # We can't actually change ppid; skip if we happen to be reaped
        # by init (e.g. running under tini).
        if os.getppid() == 1:
            pytest.skip("test process is reaped by init")
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        assert ctx["under_systemd"] is False


    def test_detects_takeover_marker_for_self(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        marker = tmp_path / ".gateway-takeover.json"
        marker.write_text(
            f'{{"target_pid": {os.getpid()}, "replacer_pid": 99999}}',
            encoding="utf-8",
        )
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        assert "takeover_marker" in ctx
        assert ctx["takeover_marker_for_self"] is True


# ---------------------------------------------------------------------------
# format_context_for_log / context_as_json
# ---------------------------------------------------------------------------

class TestFormatters:


    def test_context_as_json_handles_unserialisable_values(self):
        ctx = {"signal": "SIGTERM", "weird": object()}
        payload = sf.context_as_json(ctx)
        # default=str means objects get repr'd, JSON stays valid
        decoded = json.loads(payload)
        assert decoded["signal"] == "SIGTERM"
        assert "weird" in decoded


# ---------------------------------------------------------------------------
# spawn_async_diagnostic
# ---------------------------------------------------------------------------

class TestSpawnAsyncDiagnostic:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only diagnostic")
    def test_spawns_subprocess_and_writes_output(self, tmp_path):
        log_path = tmp_path / "diag.log"
        pid = sf.spawn_async_diagnostic(log_path, "SIGTERM", timeout_seconds=3.0)
        assert pid is not None and pid > 0

        # Wait briefly for the subprocess to write — bounded by its own timeout.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if log_path.exists() and log_path.stat().st_size > 0:
                # Wait a touch longer for the script to finish writing
                time.sleep(0.2)
                break
            time.sleep(0.1)

        # Reap the subprocess so it doesn't show up as a zombie.
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass

        assert log_path.exists()
        contents = log_path.read_text(encoding="utf-8", errors="replace")
        assert "shutdown diagnostic" in contents
        assert "SIGTERM" in contents


# ---------------------------------------------------------------------------
# _parse_systemd_duration_to_us
# ---------------------------------------------------------------------------

class TestParseSystemdDuration:
    def test_seconds(self):
        assert sf._parse_systemd_duration_to_us("90s") == 90 * 1_000_000

    def test_minutes(self):
        assert sf._parse_systemd_duration_to_us("3min") == 180 * 1_000_000


# ---------------------------------------------------------------------------
# check_systemd_timing_alignment
# ---------------------------------------------------------------------------

class TestCheckSystemdTimingAlignment:

    def test_returns_none_when_unit_undeterminable(self, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "abc")
        # /proc/self/cgroup likely doesn't end in .service for the test runner
        result = sf.check_systemd_timing_alignment(180.0)
        # Either None (we couldn't find a unit) or a dict with mismatch info
        # for whatever unit pytest IS in.  Both are valid; we just ensure
        # the function doesn't raise.
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# capture_residual_children — final-boundary child-process forensic snapshot
# ---------------------------------------------------------------------------

class TestCaptureResidualChildren:
    """Bounded, privacy-preserving snapshot of remaining gateway-cgroup pids."""

    def test_remaining_pid_produces_bounded_redacted_record_and_artifact(
        self, tmp_path
    ):
        artifact_path = tmp_path / "logs" / "gateway-shutdown-children.json"

        def fake_pids():
            return [os.getpid()]  # self only → excluded, nothing residual

        def fake_registry_pids():
            return [os.getpid()]

        captured = sf.capture_residual_children(
            phase="pre_lock_release",
            deadline_remaining=1.5,
            remaining_pids_fn=fake_pids,
            registered_pids_fn=fake_registry_pids,
            artifact_path=artifact_path,
        )

        # Self is excluded → no residual children → no artifact.
        assert captured is None
        assert not artifact_path.exists()

        # Now with a real non-self pid (use the pytest parent process).
        parent_pid = os.getppid()

        def fake_pids_with_parent():
            return [os.getpid(), parent_pid]

        artifact2 = tmp_path / "logs2" / "gateway-shutdown-children.json"
        captured2 = sf.capture_residual_children(
            phase="pre_lock_release",
            deadline_remaining=2.0,
            remaining_pids_fn=fake_pids_with_parent,
            registered_pids_fn=lambda: [],
            artifact_path=artifact2,
        )
        assert captured2 is not None
        data2 = json.loads(artifact2.read_text(encoding="utf-8"))
        assert len(data2["children"]) == 1
        rec = data2["children"][0]
        assert rec["pid"] == parent_pid
        # bounded record: only allowlisted keys
        allowed = {
            "pid",
            "ppid",
            "name",
            "state",
            "start_ticks",
            "command",
            "registered",
        }
        assert set(rec.keys()) <= allowed
        # redacted command: basename or opaque marker, never raw argv
        assert rec["command"] != ""
        assert " " not in rec["command"] or rec["command"] == "[redacted]"
        # registry membership recorded and False here
        assert rec["registered"] is False

    def test_registered_flag_marks_registry_pids(self, tmp_path):
        parent_pid = os.getppid()
        artifact = tmp_path / "gateway-shutdown-children.json"
        sf.capture_residual_children(
            phase="final",
            deadline_remaining=0.5,
            remaining_pids_fn=lambda: [parent_pid],
            registered_pids_fn=lambda: [parent_pid, 999999],
            artifact_path=artifact,
        )
        data = json.loads(artifact.read_text(encoding="utf-8"))
        rec = data["children"][0]
        assert rec["registered"] is True

    def test_no_remaining_pids_writes_no_artifact(self, tmp_path):
        artifact = tmp_path / "logs" / "gateway-shutdown-children.json"

        def fake_pids():
            return [os.getpid()]  # only self → nothing remaining

        result = sf.capture_residual_children(
            phase="pre_lock_release",
            deadline_remaining=1.0,
            remaining_pids_fn=fake_pids,
            registered_pids_fn=lambda: [],
            artifact_path=artifact,
        )
        assert result is None
        assert not artifact.exists()

    def test_unreadable_proc_and_failed_write_never_raise(self, tmp_path):
        # remaining_pids_fn raising must be swallowed
        def boom():
            raise OSError("proc wedged")

        assert (
            sf.capture_residual_children(
                phase="pre_lock_release",
                deadline_remaining=1.0,
                remaining_pids_fn=boom,
                registered_pids_fn=lambda: [],
                artifact_path=tmp_path / "x.json",
            )
            is None
        )

        # write to an impossible path must be swallowed
        assert (
            sf.capture_residual_children(
                phase="pre_lock_release",
                deadline_remaining=1.0,
                remaining_pids_fn=lambda: [os.getppid()],
                registered_pids_fn=lambda: [],
                artifact_path=tmp_path / "nope" / "\x00bad" / "x.json",
            )
            is None
        )

        # registry probe raising must be swallowed
        def registry_boom():
            raise RuntimeError("registry locked")

        result = sf.capture_residual_children(
            phase="pre_lock_release",
            deadline_remaining=1.0,
            remaining_pids_fn=lambda: [os.getppid()],
            registered_pids_fn=registry_boom,
            artifact_path=tmp_path / "y.json",
        )
        assert result is None or isinstance(result, dict)
        if result is not None:
            data = json.loads((tmp_path / "y.json").read_text(encoding="utf-8"))
            assert data["children"][0].get("registered") is None

    def test_default_remaining_pids_fn_reads_proc_children_of_self(self, tmp_path):
        # Default /proc-based discovery: returns at least a list, never raises.
        result = sf.capture_residual_children(
            phase="test",
            deadline_remaining=1.0,
            registered_pids_fn=lambda: [],
            artifact_path=tmp_path / "z.json",
        )
        # May or may not write depending on live children; must not raise and
        # must return dict|None.
        assert result is None or isinstance(result, dict)
