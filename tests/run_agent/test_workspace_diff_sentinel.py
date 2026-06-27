from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.workspace_diff_sentinel import (
    WorkspaceDiffSnapshot,
    _count_untracked_lines,
    _get_sentinel_config,
    _sentinel_enabled,
    build_workspace_diff_footer,
    compute_workspace_diff_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(
    root: Path,
    entries: dict | None = None,
    stat_lines: list | None = None,
    total_insertions: int = 0,
    total_deletions: int = 0,
    total_changed_files: int = 0,
) -> WorkspaceDiffSnapshot:
    return WorkspaceDiffSnapshot(
        root=root,
        entries=entries or {},
        stat_lines=stat_lines or [],
        total_insertions=total_insertions,
        total_deletions=total_deletions,
        total_changed_files=total_changed_files,
    )


# ---------------------------------------------------------------------------
# MEDIUM-2: enabled gate
# ---------------------------------------------------------------------------

class TestSentinelEnabled:
    """Verify _sentinel_enabled respects config and defaults to True."""

    def test_default_is_enabled(self):
        """When no config is present, the sentinel defaults to enabled."""
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
            assert _sentinel_enabled() is True

    def test_config_enabled_true(self):
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"enabled": True}):
            assert _sentinel_enabled() is True

    def test_config_enabled_false(self):
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"enabled": False}):
            assert _sentinel_enabled() is False


# ---------------------------------------------------------------------------
# MEDIUM-1: config thresholds
# ---------------------------------------------------------------------------

class TestConfigThresholds:
    """Verify build_workspace_diff_footer reads thresholds from config."""

    def test_hardcoded_defaults_match_config(self):
        """When config is unavailable, defaults match hermes_cli/config.py values."""
        before = _make_snapshot(Path("/tmp/repo"))
        # 6 files changed, 600 ins, 600 dels → exceeds default 5/500/500/800
        after = _make_snapshot(
            Path("/tmp/repo"),
            entries={
                f"file{i}.txt": {"status": "M", "insertions": 100, "deletions": 100}
                for i in range(6)
            },
            stat_lines=[f" M file{i}.txt" for i in range(6)],
            total_insertions=600,
            total_deletions=600,
            total_changed_files=6,
        )
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
            footer = build_workspace_diff_footer(before, after)
        assert footer  # above thresholds → non-empty footer

    def test_custom_max_changed_files(self):
        """Raising max_changed_files should suppress the footer when files are below it."""
        before = _make_snapshot(Path("/tmp/repo"))
        after = _make_snapshot(
            Path("/tmp/repo"),
            entries={
                f"file{i}.txt": {"status": "M", "insertions": 50, "deletions": 50}
                for i in range(6)
            },
            stat_lines=[f" M file{i}.txt" for i in range(6)],
            total_insertions=300,
            total_deletions=300,
            total_changed_files=6,
        )
        # All thresholds raised: 6 files < 10, 300 < 500 ins, 300 < 500 dels, 600 < 800 total
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"max_changed_files": 10}):
            footer = build_workspace_diff_footer(before, after)
        assert footer == ""

    def test_custom_max_insertions(self):
        """Raising max_insertions should suppress the footer when ins are below it."""
        before = _make_snapshot(Path("/tmp/repo"))
        after = _make_snapshot(
            Path("/tmp/repo"),
            entries={
                "big.txt": {"status": "M", "insertions": 600, "deletions": 0},
            },
            stat_lines=[" M big.txt"],
            total_insertions=600,
            total_deletions=0,
            total_changed_files=1,
        )
        # max_insertions=700 → 600 ins is below → no footer (other thresholds also pass)
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"max_insertions": 700, "max_total_lines": 1000}):
            footer = build_workspace_diff_footer(before, after)
        assert footer == ""

    def test_custom_max_deletions(self):
        """Raising max_deletions should suppress the footer when dels are below it."""
        before = _make_snapshot(Path("/tmp/repo"))
        after = _make_snapshot(
            Path("/tmp/repo"),
            entries={
                "big.txt": {"status": "M", "insertions": 0, "deletions": 600},
            },
            stat_lines=[" M big.txt"],
            total_insertions=0,
            total_deletions=600,
            total_changed_files=1,
        )
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"max_deletions": 700, "max_total_lines": 1000}):
            footer = build_workspace_diff_footer(before, after)
        assert footer == ""

    def test_custom_max_total_lines(self):
        """Raising max_total_lines should suppress the footer when ins+dels are below it."""
        before = _make_snapshot(Path("/tmp/repo"))
        after = _make_snapshot(
            Path("/tmp/repo"),
            entries={
                "big.txt": {"status": "M", "insertions": 400, "deletions": 400},
            },
            stat_lines=[" M big.txt"],
            total_insertions=400,
            total_deletions=400,
            total_changed_files=1,
        )
        # Default thresholds: 1 file (≤5), 400 ins (≤500), 400 dels (≤500),
        # but 800 total (≤800) → borderline. With max_total_lines=900, still ≤900.
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"max_total_lines": 900}):
            footer = build_workspace_diff_footer(before, after)
        assert footer == ""

    def test_include_stat_lines_config(self):
        """include_stat_lines should limit how many stat lines appear in the footer."""
        before = _make_snapshot(Path("/tmp/repo"))
        entries = {
            f"file{i}.txt": {"status": "M", "insertions": 100, "deletions": 100}
            for i in range(10)
        }
        after = _make_snapshot(
            Path("/tmp/repo"),
            entries=entries,
            stat_lines=[f" M file{i}.txt" for i in range(10)],
            total_insertions=1000,
            total_deletions=1000,
            total_changed_files=10,
        )
        # include_stat_lines=3 → only 3 bullet lines in footer
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"include_stat_lines": 3}):
            footer = build_workspace_diff_footer(before, after)
        bullet_count = sum(1 for line in footer.split("\n") if line.startswith("•"))
        assert bullet_count == 3


# ---------------------------------------------------------------------------
# Existing baseline tests (preserved)
# ---------------------------------------------------------------------------

def test_preexisting_dirty_file_does_not_alert(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "tracked.txt").write_text("old\n")

    baseline = WorkspaceDiffSnapshot(
        root=repo,
        entries={"tracked.txt": {"status": "M", "insertions": 1, "deletions": 0}},
        stat_lines=[" M tracked.txt"],
        total_insertions=1,
        total_deletions=0,
        total_changed_files=1,
    )
    with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
        assert build_workspace_diff_footer(baseline, baseline) == ""


def test_new_untracked_and_modified_files_alert(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "tracked.txt").write_text("old\n")
    (repo / "new.txt").write_text("fresh\n")

    before = WorkspaceDiffSnapshot(root=repo, entries={}, stat_lines=[], total_insertions=0, total_deletions=0, total_changed_files=0)
    after = WorkspaceDiffSnapshot(
        root=repo,
        entries={
            "tracked.txt": {"status": "M", "insertions": 600, "deletions": 5},
            "new.txt": {"status": "?", "insertions": 1, "deletions": 0},
        },
        stat_lines=[" M tracked.txt", "?? new.txt"],
        total_insertions=601,
        total_deletions=5,
        total_changed_files=2,
    )
    with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
        footer = build_workspace_diff_footer(before, after)
    assert footer
    assert "broad workspace mutation" in footer
    assert "2 file(s)" in footer
    assert "new.txt" in footer


def test_non_git_dir_skips(tmp_path: Path):
    snapshot = compute_workspace_diff_snapshot(tmp_path)
    assert snapshot is None


# ---------------------------------------------------------------------------
# Integration-ish tests (real git repos, real files)
# ---------------------------------------------------------------------------

class TestRealGitIntegration:
    """Exercise compute_workspace_diff_snapshot against real git repos."""

    def _init_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        # Configure git so commits work.
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        return repo

    def test_real_untracked_file_counts_lines(self, tmp_path: Path):
        """An untracked text file should have its line count reflected."""
        repo = self._init_repo(tmp_path)
        (repo / "untracked.txt").write_text("line1\nline2\nline3\n")

        snapshot = compute_workspace_diff_snapshot(repo)
        assert snapshot is not None
        assert "untracked.txt" in snapshot.entries
        assert snapshot.entries["untracked.txt"]["status"] == "??"
        assert snapshot.entries["untracked.txt"]["insertions"] == 3
        assert snapshot.entries["untracked.txt"].get("untracked_bytes", 0) > 0

    def test_untracked_binary_file_skipped(self, tmp_path: Path):
        """Binary untracked files should report 0 insertions / 0 bytes."""
        repo = self._init_repo(tmp_path)
        (repo / "binary.dat").write_bytes(b"\x00\x01\x02\x03")

        snapshot = compute_workspace_diff_snapshot(repo)
        assert snapshot is not None
        assert snapshot.entries["binary.dat"]["insertions"] == 0
        assert snapshot.entries["binary.dat"].get("untracked_bytes", 0) == 0

    def test_untracked_file_threshold_footer(self, tmp_path: Path):
        """A large untracked file should trip the max_total_lines threshold."""
        repo = self._init_repo(tmp_path)
        # Write 1000 lines → exceeds default max_total_lines=800
        (repo / "huge.txt").write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")

        before = compute_workspace_diff_snapshot(repo)
        # Add another untracked file after the before snapshot.
        (repo / "huge2.txt").write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")
        after = compute_workspace_diff_snapshot(repo)

        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
            footer = build_workspace_diff_footer(before, after)
        assert footer
        assert "broad workspace mutation" in footer
        assert "huge2.txt" in footer

    def test_enabled_false_no_snapshot(self, tmp_path: Path, monkeypatch):
        """When enabled=false, the caller should skip the sentinel entirely."""
        repo = self._init_repo(tmp_path)
        (repo / "tracked.txt").write_text("old\n")
        import subprocess
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        # Simulate the caller's gate by skipping snapshot collection entirely.
        before = None
        (repo / "new.txt").write_text("fresh\n")
        after = compute_workspace_diff_snapshot(repo)
        assert build_workspace_diff_footer(before, after) == ""

    def test_config_thresholds_override_defaults(self, tmp_path: Path):
        """Custom thresholds from config should be respected in real repos."""
        repo = self._init_repo(tmp_path)
        (repo / "a.txt").write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
        (repo / "b.txt").write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
        import subprocess
        subprocess.run(["git", "add", "a.txt", "b.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "seed tracked files"], cwd=repo, check=True, capture_output=True)

        before = compute_workspace_diff_snapshot(repo)
        # Modify one file.
        (repo / "a.txt").write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
        after = compute_workspace_diff_snapshot(repo)

        # With default thresholds (max_insertions=500), 100 lines is below → no footer.
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
            assert build_workspace_diff_footer(before, after) == ""

        # With lowered threshold (max_insertions=50), it should alert.
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={"max_insertions": 50}):
            footer = build_workspace_diff_footer(before, after)
        assert footer
        assert "broad workspace mutation" in footer

    def test_staged_large_add_triggers_footer(self, tmp_path: Path):
        """A newly created large file followed by git add must show insertions > 0 and trigger footer."""
        repo = self._init_repo(tmp_path)
        (repo / "seed.txt").write_text("seed\n")
        import subprocess
        subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        before = compute_workspace_diff_snapshot(repo)
        # Create a 1000-line file and stage it.
        (repo / "big_staged.txt").write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")
        subprocess.run(["git", "add", "big_staged.txt"], cwd=repo, check=True, capture_output=True)
        after = compute_workspace_diff_snapshot(repo)

        assert after is not None
        assert "big_staged.txt" in after.entries
        # Staged new file appears as "A" in porcelain.
        assert after.entries["big_staged.txt"]["status"] == "A"
        # The critical bug: before fix, insertions were 0 because only unstaged numstat was used.
        assert after.entries["big_staged.txt"]["insertions"] == 1000
        assert after.total_insertions >= 1000

        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
            footer = build_workspace_diff_footer(before, after)
        assert footer
        assert "broad workspace mutation" in footer
        assert "big_staged.txt" in footer

    def test_footer_shows_new_file_among_many_preexisting_dirty(self, tmp_path: Path):
        """When many preexisting dirty files exist, footer must still include the new triggering file."""
        repo = self._init_repo(tmp_path)
        import subprocess
        # Create and commit a tracked file so repo isn't empty.
        (repo / "seed.txt").write_text("seed\n")
        subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        # Create 25 preexisting dirty files (names sort before 'z_trigger.txt').
        for i in range(25):
            (repo / f"dirty_{i:02d}.txt").write_text(f"dirty {i}\n")
        before = compute_workspace_diff_snapshot(repo)

        # Now add one new triggering file whose name sorts after all dirty files.
        (repo / "z_trigger.txt").write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")
        after = compute_workspace_diff_snapshot(repo)

        # Default include_stat_lines=20, so with 25 preexisting dirty files the old logic
        # (using after.stat_lines) would show only the first 20 status lines and omit z_trigger.
        with patch("agent.workspace_diff_sentinel._get_sentinel_config", return_value={}):
            footer = build_workspace_diff_footer(before, after)
        assert footer
        assert "broad workspace mutation" in footer
        assert "z_trigger.txt" in footer


# ---------------------------------------------------------------------------
# _count_untracked_lines unit tests
# ---------------------------------------------------------------------------

class TestCountUntrackedLines:
    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.txt"
        p.write_text("")
        assert _count_untracked_lines(p) == (0, 0)

    def test_three_lines(self, tmp_path: Path):
        p = tmp_path / "three.txt"
        p.write_text("a\nb\nc\n")
        assert _count_untracked_lines(p) == (3, 6)

    def test_no_trailing_newline(self, tmp_path: Path):
        p = tmp_path / "notrail.txt"
        p.write_text("a\nb")
        assert _count_untracked_lines(p) == (2, 3)

    def test_binary_file(self, tmp_path: Path):
        p = tmp_path / "bin.dat"
        p.write_bytes(b"\x00\x01\x02")
        assert _count_untracked_lines(p) == (0, 0)

    def test_large_file_capped(self, tmp_path: Path):
        p = tmp_path / "big.txt"
        p.write_text("x\n" * 3_000_000)
        ins, bts = _count_untracked_lines(p)
        assert ins > 0
        assert bts <= 2_000_000
