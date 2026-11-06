"""Private stage capability refusal precedes any source or output access."""
import argparse

import pytest

from hermes_cli import insights_refresh_stage as module


@pytest.mark.parametrize('missing', ['geteuid', 'getegid', 'O_DIRECTORY', 'O_NOFOLLOW',
                                     'dir_fd', 'follow_symlinks'])
def test_missing_capability_refuses_before_io(missing, monkeypatch, capsys):
    if missing == 'dir_fd':
        monkeypatch.setattr(module.os, 'supports_dir_fd', set())
    elif missing == 'follow_symlinks':
        monkeypatch.setattr(module.os, 'supports_follow_symlinks', set())
    else:
        monkeypatch.delattr(module.os, missing, raising=False)

    def unexpected_io(*args, **kwargs):
        pytest.fail('unsupported private stage attempted filesystem I/O')

    monkeypatch.setattr(module.os, 'open', unexpected_io)
    monkeypatch.setattr(module.Path, 'resolve', unexpected_io)
    assert module.cmd_refresh_stage(argparse.Namespace()) == 1
    monkeypatch.undo()
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == 'Insights private stage refresh refused\n'


@pytest.mark.platforms('windows')
def test_native_windows_refuses_before_source_access(capsys):
    assert module.cmd_refresh_stage(argparse.Namespace()) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == 'Insights private stage refresh refused\n'
