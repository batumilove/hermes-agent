"""Offline evidence-only controls; no installer, token, registry or provider calls."""
import json
import subprocess
import sys

import pytest

from tests.e2e.core.windows._helpers import Run
from tests.e2e.core.windows_update._machine import Journey, Machine


@pytest.mark.parametrize("failure", [RuntimeError("EXCEPTION_SECRET"), subprocess.TimeoutExpired("SECRET_ARGV", 1500)])
def test_unread_install_failure_is_serialized(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setenv("HERMES_LAUNCHER_DIAGNOSTIC", "1")
    m = Machine(tmp_path, "profile", "unused")
    (m.logs / "01-install.log").write_text("installer progress\n" * 2000)
    j = Journey(m)

    def fail():
        raise failure

    assert j.step("install", fail) is failure
    assert not j.ok("install")
    assert not j.ok("install_ok")
    assert "update" not in j.results
    payload = (m.logs / "install-prerequisite.json").read_text()
    row = json.loads(payload)
    assert row["phase"] == "install"
    assert row["failure_class"] == type(failure).__name__
    assert row["timed_out"] is isinstance(failure, subprocess.TimeoutExpired)
    assert row["exit_code"] is None
    assert row["installer_log_tail"].endswith("installer progress\n")
    assert len(row["installer_log_tail"]) <= 6000
    assert "SECRET" not in payload
    assert capsys.readouterr() == ("", "")


def test_nonzero_install_gate_evidence_and_default_noop(tmp_path, monkeypatch, capsys):
    m = Machine(tmp_path, "profile", "unused")
    j = Journey(m)
    run = Run(23, "SECRET_OUTPUT", "")
    monkeypatch.delenv("HERMES_LAUNCHER_DIAGNOSTIC", raising=False)
    assert j.step("install", lambda: run) is run
    assert not (m.logs / "install-prerequisite.json").exists()
    monkeypatch.setenv("HERMES_LAUNCHER_DIAGNOSTIC", "1")
    assert j.step("install", lambda: run) is run
    failure = j.step("install_ok", lambda: j.require("install", False, "SECRET_MESSAGE", run))
    assert isinstance(failure, RuntimeError)
    assert not j.ok("install_ok")
    row = json.loads((m.logs / "install-prerequisite.json").read_text())
    assert row["phase"] == "install_ok"
    assert row["exit_code"] == 23
    assert row["failure_class"] == "RuntimeError"
    assert "SECRET" not in json.dumps(row)
    assert capsys.readouterr() == ("", "")


def test_capture_io_failure_does_not_mask_original(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_LAUNCHER_DIAGNOSTIC", "1")
    m = Machine(tmp_path, "profile", "unused")
    (m.logs / "install-prerequisite.json").mkdir()
    failure = RuntimeError("original")
    j = Journey(m)
    def fail():
        raise failure
    assert j.step("install", fail) is failure
    assert not j.ok("install")


def test_real_logged_timeout_is_retained_after_assertion_conversion(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_LAUNCHER_DIAGNOSTIC", "1")
    m = Machine(tmp_path, "profile", "unused")
    m.profile.mkdir(parents=True)
    # Only the Windows cleanup command is a stand-in. The child and wait timeout are real.
    monkeypatch.setattr(m, "evidence", lambda: "fixture evidence")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    j = Journey(m)
    value = j.step("install", lambda: m._run_logged(
        [sys.executable, "-c", "import time; print('useful progress', flush=True); time.sleep(.2)"],
        "install", timeout=.05))
    assert isinstance(value, AssertionError)
    assert not j.ok("install") and not j.ok("install_ok")
    row = json.loads((m.logs / "install-prerequisite.json").read_text())
    assert row["failure_class"] == "AssertionError"
    assert row["timed_out"] is True
    assert row["exit_code"] is None
    assert "useful progress" in row["installer_log_tail"]
    assert capsys.readouterr() == ("", "")
