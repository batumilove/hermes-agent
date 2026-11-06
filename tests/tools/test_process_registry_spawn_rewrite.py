"""Command rewriting at ProcessRegistry's pipe and PTY launch boundaries."""

from unittest.mock import MagicMock, patch

import pytest

from tools.process_registry import ProcessRegistry


@pytest.fixture
def registry(monkeypatch):
    """Use a fresh registry without probing the host's systemd user manager."""
    monkeypatch.setattr("tools.process_registry._SYSTEMD_SCOPE_AVAILABLE", False)
    return ProcessRegistry()


class TestSpawnRewriteCompoundBackground:
    """Verify that spawn_local rewrites `A && B &` patterns to avoid subshell deadlocks.

    Issue #68915: when bash parses ``A && B &`` it forks a subshell ``(A && B) &``.
    If B is a long-running server, the subshell never exits and holds the stdout
    pipe open, causing a permanent deadlock. The rewriter wraps the tail to
    ``A && { B & }`` so no subshell fork occurs.
    """

    def test_compound_and_background_gets_rewritten(self, registry):
        """A && B & must be rewritten to A && { B & } before spawning the job."""
        captured_cmd = []

        def fake_spawn_job(_session, args, _persist_on_release, **kwargs):
            captured_cmd.append(args)
            proc = MagicMock()
            proc.pid = 1111
            proc.stdout = MagicMock()
            return proc

        # Capture only this registry launch: an owner-death supervisor may also
        # call Popen, depending on process state. Ownership is tested separately.
        with patch("tools.process_registry._find_shell", return_value="/bin/bash"), \
             patch.object(registry, "_spawn_job", side_effect=fake_spawn_job), \
             patch.object(registry, "_track_started"), \
             patch.object(registry, "_write_checkpoint"):
            registry.spawn_local("cd /app && node server.js &>/tmp/srv.log &", cwd="/tmp")

        assert len(captured_cmd) == 1
        shell_cmd = captured_cmd[0]
        # The command passed to Popen should be the REWRITTEN version
        assert "&& { node server.js &>/tmp/srv.log & }" in shell_cmd[2]


    def test_pty_path_uses_rewritten_command(self, registry):
        """PTY spawn path must also use the rewritten command (issue #68915)."""
        mock_pty_proc = MagicMock()
        mock_pty_proc.pid = 5555

        mock_pty_module = MagicMock()
        mock_pty_module.PtyProcess.spawn = MagicMock(return_value=mock_pty_proc)

        with patch("tools.process_registry._find_shell", return_value="/bin/bash"), \
             patch.dict("sys.modules", {"ptyprocess": mock_pty_module}), \
             patch.object(registry, "_own_pty_job"), \
             patch.object(registry, "_track_started"), \
             patch.object(registry, "_write_checkpoint"):
            session = registry.spawn_local(
                "cd /app && node server.js &",
                cwd="/tmp",
                use_pty=True,
            )

        assert mock_pty_module.PtyProcess.spawn.called, \
            "PTY spawn should have been attempted"
        pty_args = mock_pty_module.PtyProcess.spawn.call_args[0][0]
        assert "&& { node server.js & }" in pty_args[2], \
            f"PTY path should use rewritten command, got: {pty_args[2]}"
        assert session.command == "cd /app && node server.js &"

