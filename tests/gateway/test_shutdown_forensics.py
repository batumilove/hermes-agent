"""Tests for gateway.shutdown_forensics — fast snapshot + async diag spawn."""

from __future__ import annotations

import builtins
import json
import os
import signal
import shutil
import sys
import subprocess

import pytest

from gateway import shutdown_forensics as sf
from gateway import shutdown_residual_children as residual
from tests.waiting import wait_until

# ---------------------------------------------------------------------------
# _signal_name
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# snapshot_shutdown_context
# ---------------------------------------------------------------------------

class TestSnapshotShutdownContext:

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
# persisted snapshots must never include process argv (#112459)
# ---------------------------------------------------------------------------

_ARGV_CANARY = "lin_api_CANARY_SHUTDOWN_FORENSICS_9f3a2c"

@pytest.fixture
def child_with_secret_argv():
    """A live child whose argv carries a token-shaped value, like ``docker exec -e KEY=...``."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()", f"--token={_ARGV_CANARY}"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()

class TestArgvFreePersistence:

    @pytest.mark.platforms("linux")
    def test_snapshot_and_log_line_identify_process_without_argv(self, child_with_secret_argv):
        """/proc-backed summaries keep pid/name/ppid/state but never the command line, so neither
        the JSON snapshot nor the warning line can carry a credential from a parent's argv."""
        summary = sf._proc_summary(child_with_secret_argv.pid)
        assert summary["pid"] == child_with_secret_argv.pid
        assert summary["name"]  # identity survives
        assert "cmdline" not in summary

        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        ctx["parent"] = summary
        line = sf.format_context_for_log(ctx)
        assert _ARGV_CANARY not in line and _ARGV_CANARY not in sf.context_as_json(ctx)
        assert f"parent_pid={child_with_secret_argv.pid}" in line

# ---------------------------------------------------------------------------
# spawn_async_diagnostic
# ---------------------------------------------------------------------------

def _wait_for_diagnostic(pid: int, *, timeout: float) -> None:
    def reaped() -> bool:
        try:
            return os.waitpid(pid, os.WNOHANG)[0] == pid
        except ChildProcessError:
            return True

    wait_until(reaped, timeout=timeout, what="detached shutdown diagnostic exited")


class TestSpawnAsyncDiagnostic:
    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX-only diagnostic"
    )
    @pytest.mark.skipif(
        shutil.which("timeout") is None,
        reason="diagnostic shells out to GNU timeout (coreutils); macOS hosts without coreutils cannot spawn it",
    )
    def test_spawns_subprocess_and_writes_output(self, tmp_path):
        self._assert_diagnostic_written(tmp_path)

    @pytest.mark.platforms("macos")
    def test_spawns_without_gnu_timeout_on_macos(self, tmp_path):
        """Stock macOS has no ``timeout`` binary and BSD ``ps``; the diagnostic still lands."""
        self._assert_diagnostic_written(tmp_path)

    @staticmethod
    def _assert_diagnostic_written(tmp_path):
        log_path = tmp_path / "diag.log"
        pid = sf.spawn_async_diagnostic(log_path, "SIGTERM", timeout_seconds=3.0)
        assert pid is not None and pid > 0

        _wait_for_diagnostic(pid, timeout=5.0)

        assert log_path.exists()
        contents = log_path.read_text(encoding="utf-8", errors="replace")
        assert "shutdown diagnostic" in contents
        assert "SIGTERM" in contents
        lines = contents.splitlines()
        ps_section = lines[lines.index("--- ps (top 60 by cpu, comm only) ---") + 1:]
        assert ps_section and ps_section[0].split()[:2] == ["PID", "PPID"], \
            "ps column header must lead the listing, not sort as a 0.0-cpu row"

    @pytest.mark.platforms("linux")
    def test_diagnostic_log_omits_child_argv_and_is_owner_only(self, tmp_path, child_with_secret_argv):
        """The detached ps/pstree walk must not write any process's argv to disk, and the log
        (even one created 0644 by an earlier release) ends up owner-only."""
        log_path = tmp_path / "diag.log"
        log_path.write_text("prior\n", encoding="utf-8")
        os.chmod(log_path, 0o644)

        pid = sf.spawn_async_diagnostic(log_path, "SIGTERM", timeout_seconds=5.0)
        assert pid is not None
        _wait_for_diagnostic(pid, timeout=8.0)

        contents = log_path.read_text(encoding="utf-8", errors="replace")
        assert "shutdown diagnostic" in contents
        assert _ARGV_CANARY not in contents
        assert (log_path.stat().st_mode & 0o777) == 0o600

# ---------------------------------------------------------------------------
# parse_systemd_duration_to_us
# ---------------------------------------------------------------------------

class TestParseSystemdDuration:
    def test_seconds(self):
        assert sf.parse_systemd_duration_to_us("90s") == 90 * 1_000_000

    def test_minutes(self):
        assert sf.parse_systemd_duration_to_us("3min") == 180 * 1_000_000

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

        captured = residual.capture_residual_children(
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
        captured2 = residual.capture_residual_children(
            phase="pre_lock_release",
            deadline_remaining=2.0,
            remaining_pids_fn=fake_pids_with_parent,
            registered_pids_fn=list,
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

    def test_default_artifact_uses_profile_scope_and_limits_residual_members(self, tmp_path, monkeypatch):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override

        default_home = tmp_path / "default"
        profile_home = tmp_path / "reviewer"
        monkeypatch.setenv("HERMES_HOME", str(default_home))
        token = set_hermes_home_override(profile_home)
        try:
            captured = residual.capture_residual_children(
                phase="pre_lock_release",
                deadline_remaining=0.05,
                remaining_pids_fn=lambda: [os.getpid(), *([os.getppid()] * 70)],
                registered_pids_fn=lambda: {os.getppid()},
            )
        finally:
            reset_hermes_home_override(token)

        assert captured is not None
        assert len(captured["children"]) == 64
        assert all(child["registered"] for child in captured["children"])
        assert not (default_home / "logs" / "gateway-shutdown-children.json").exists()
        artifact = profile_home / "logs" / "gateway-shutdown-children.json"
        assert json.loads(artifact.read_text()) == captured

    def test_registered_flag_marks_registry_pids(self, tmp_path):
        parent_pid = os.getppid()
        artifact = tmp_path / "gateway-shutdown-children.json"
        residual.capture_residual_children(
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

        result = residual.capture_residual_children(
            phase="pre_lock_release",
            deadline_remaining=1.0,
            remaining_pids_fn=fake_pids,
            registered_pids_fn=list,
            artifact_path=artifact,
        )
        assert result is None
        assert not artifact.exists()

    def test_unreadable_proc_and_failed_write_never_raise(self, tmp_path):
        # remaining_pids_fn raising must be swallowed
        def boom():
            raise OSError("proc wedged")

        assert (
            residual.capture_residual_children(
                phase="pre_lock_release",
                deadline_remaining=1.0,
                remaining_pids_fn=boom,
                registered_pids_fn=list,
                artifact_path=tmp_path / "x.json",
            )
            is None
        )

        # write to an impossible path must be swallowed
        assert (
            residual.capture_residual_children(
                phase="pre_lock_release",
                deadline_remaining=1.0,
                remaining_pids_fn=lambda: [os.getppid()],
                registered_pids_fn=list,
                artifact_path=tmp_path / "nope" / "\x00bad" / "x.json",
            )
            is None
        )

        # registry probe raising must be swallowed
        def registry_boom():
            raise RuntimeError("registry locked")

        result = residual.capture_residual_children(
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
        # Default cgroup-based discovery: returns at least a list, never raises.
        result = residual.capture_residual_children(
            phase="test",
            deadline_remaining=1.0,
            registered_pids_fn=list,
            artifact_path=tmp_path / "z.json",
        )
        # May or may not write depending on live children; must not raise and
        # must return dict|None.
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# _default_gateway_remaining_pids — bounded cgroup-v2 cgroup.procs discovery
# ---------------------------------------------------------------------------

class TestDefaultGatewayRemainingPidsBoundedCgroup:
    """Discovery must read exactly one cgroup.procs file, never /proc scans."""

    def _install(self, monkeypatch, tmp_path, cgroup_self_lines, procs_content):
        procs_file = tmp_path / "cgroup.procs"
        procs_file.write_text(procs_content, encoding="utf-8")
        self_file = tmp_path / "self_cgroup"
        self_file.write_text(cgroup_self_lines, encoding="utf-8")
        reads = {"cgroup_self": 0, "cgroup_procs": 0, "proc_entries": []}
        real_open = builtins.open


        def fake_open(path, *args, **kwargs):
            p = str(path)
            if p == "/proc/self/cgroup":
                reads["cgroup_self"] += 1
                return real_open(self_file, *args, **kwargs)
            if p.endswith("/cgroup.procs"):
                reads["cgroup_procs"] += 1
                return real_open(procs_file, *args, **kwargs)
            raise AssertionError(f"unexpected read: {p}")

        monkeypatch.setattr(builtins, "open", fake_open)

        def fake_listdir(path):
            if str(path) == "/proc":
                reads["proc_entries"].append("listed")
                raise AssertionError("global /proc enumeration is forbidden")
            raise AssertionError(f"unexpected listdir: {path}")

        monkeypatch.setattr(sf.os, "listdir", fake_listdir)
        return reads

    def test_reads_single_cgroup_procs_file_derived_from_v2_entry(
        self, monkeypatch, tmp_path
    ):
        reads = self._install(
            monkeypatch,
            tmp_path,
            cgroup_self_lines="0::/hermes-gateway.service\n",
            procs_content=f"{os.getpid()}\n4242\n4243\n",
        )
        pids = residual._default_gateway_remaining_pids()
        assert reads["cgroup_self"] == 1
        assert reads["cgroup_procs"] == 1
        assert sorted(pids) == sorted([os.getpid(), 4242, 4243])

    def test_ignores_non_v2_cgroup_lines(self, monkeypatch, tmp_path):
        # Only the 0:: entry may be used; v1 lines must not seed a path.
        reads = self._install(
            monkeypatch,
            tmp_path,
            cgroup_self_lines=(
                "10:devices:/user.slice\n"
                "2:cpu,cpuacct:/\n"
                "0::/hermes-gateway.service\n"
            ),
            procs_content="4242\n",
        )
        assert residual._default_gateway_remaining_pids() == [4242]
        assert reads["cgroup_procs"] == 1

    def test_no_v2_entry_returns_empty(self, monkeypatch, tmp_path):
        reads = self._install(
            monkeypatch,
            tmp_path,
            cgroup_self_lines="10:devices:/user.slice\n",
            procs_content="4242\n",
        )
        assert residual._default_gateway_remaining_pids() == []
        assert reads["cgroup_procs"] == 0

    def test_unreadable_cgroup_procs_returns_empty_without_raising(
        self, monkeypatch, tmp_path
    ):
        self_file = tmp_path / "self_cgroup"
        self_file.write_text("0::/hermes-gateway.service\n", encoding="utf-8")
        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if str(path) == "/proc/self/cgroup":
                return real_open(self_file, *args, **kwargs)
            if str(path).endswith("/cgroup.procs"):
                raise OSError("permission denied")
            raise AssertionError(f"unexpected read: {path}")

        monkeypatch.setattr(builtins, "open", fake_open)
        assert residual._default_gateway_remaining_pids() == []

    def test_unreadable_proc_self_cgroup_returns_empty(self, monkeypatch):

        def fake_open(path, *args, **kwargs):
            if str(path) == "/proc/self/cgroup":
                raise OSError("no such file")
            raise AssertionError(f"unexpected read: {path}")

        monkeypatch.setattr(builtins, "open", fake_open)
        assert residual._default_gateway_remaining_pids() == []


class TestRedactedCommandPrivacy:
    """Command description must never leak paths, NULs, or raw argv."""

    def test_command_has_no_slash_nul_or_raw_argv(self, monkeypatch, tmp_path):
        exe = tmp_path / "secretuser" / "binary name with spaces"
        exe.parent.mkdir()
        exe.write_bytes(b"#!/bin/sh\n")
        args = b"/tmp/secretuser/binary name with spaces\x00--password=hunter2\x00file.txt\x00"

        def fake_open(path, *a, **k):
            if str(path) == "/proc/12345/cmdline":
                import io

                return io.BytesIO(args)
            raise AssertionError(f"unexpected read: {path}")

        monkeypatch.setattr(builtins, "open", fake_open)
        cmd = residual._redacted_command_description(12345)
        assert cmd is not None and cmd != ""
        assert "/" not in cmd
        assert "\x00" not in cmd
        assert " " not in cmd
        assert "--password" not in cmd
        assert "file.txt" not in cmd
        assert "secretuser" not in cmd
        # falls back to opaque marker when nothing safe remains
        def name_open(path, *a, **k):
            if str(path) == "/proc/12345/cmdline":
                import io

                return io.BytesIO(b"\x00arg1\x00arg2\x00")
            raise AssertionError(f"unexpected read: {path}")

        monkeypatch.setattr(builtins, "open", name_open)
        monkeypatch.setattr(
            residual, "_read_proc_field", lambda pid, key: None if key == "Name" else "x"
        )
        cmd2 = residual._redacted_command_description(12345)
        assert cmd2 == "[redacted]"
