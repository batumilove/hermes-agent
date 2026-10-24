"""Exercise only stage2's ownership section on disposable filesystem state.

No entrypoint, services, real HERMES_HOME, mounts, or credentials are used.
Real cross-UID checks require root or passwordless sudo; otherwise skip.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

HOOK = Path(__file__).resolve().parents[2] / "docker/stage2-hook.sh"


@pytest.fixture
def ownership_runner():
    if os.getuid() == 0:
        pytest.skip("run as a non-root runtime user to verify EACCES")
    sudo = shutil.which("sudo")
    if not sudo or subprocess.run(
        [sudo, "-n", "true"], capture_output=True, timeout=10, check=False
    ).returncode:
        pytest.skip("real ownership test requires passwordless sudo")
    text = HOOK.read_text()
    section = text[text.index("path_has_symlink_component() {"):
                   text.index("# Always ensure logs/gateways")]
    # The host's runtime identity stands in for the image's hermes account.
    section = section.replace("hermes:hermes", f"{os.getuid()}:{os.getgid()}")
    section = section.replace("-user hermes", f"-user {os.getuid()}")
    section = section.replace("-group hermes", f"-group {os.getgid()}")

    def run(home):
        script = (f"set -eu\nHERMES_HOME={shlex.quote(str(home))}\n"
                  f"actual_hermes_uid={os.getuid()}\n" + section)
        return subprocess.run(
            [sudo, "-n", "sh", "-c", script],
            capture_output=True, text=True, timeout=10, check=True,
            env={"PATH": "/usr/bin:/bin"},
        )

    return run


def root_owned(path):
    subprocess.run(["sudo", "-n", "chown", "0:0", str(path)], check=True)


@pytest.mark.parametrize("cold_home", [False, True], ids=["warm", "cold"])
def test_install_state_repaired_without_widening_modes(
    ownership_runner, tmp_path, cold_home
):
    home = tmp_path / "home"
    state = home / "installs" / "installation-id"
    generation = state / "generations" / "one"
    generation.mkdir(parents=True)
    lock = state / ".install.lock"
    metadata = state / "state.json"
    executable = generation / "python"
    for path, mode in [(lock, 0o600), (metadata, 0o600), (executable, 0o750)]:
        path.write_text("fixture\n")
        path.chmod(mode)
        root_owned(path)
    if cold_home:
        root_owned(home)
    with pytest.raises(PermissionError):
        lock.open("r+")
    ownership_runner(home)
    for path, mode in [(lock, 0o600), (metadata, 0o600), (executable, 0o750)]:
        assert (path.stat().st_uid, path.stat().st_gid) == (os.getuid(), os.getgid())
        assert path.stat().st_mode & 0o777 == mode
        with path.open("r+") as stream:
            assert stream.read() == "fixture\n"
    # A second warm boot must remain harmless.
    ownership_runner(home)
    assert lock.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("link_kind", ["home", "installs", "nested"])
def test_install_repair_does_not_follow_symlinks(
    ownership_runner, tmp_path, link_kind
):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched")
    sentinel.chmod(0o600)
    root_owned(sentinel)
    if link_kind == "home":
        (outside / "installs").mkdir()
        sentinel.rename(outside / "installs" / "sentinel")
        sentinel = outside / "installs" / "sentinel"
        home.rmdir()
        home.symlink_to(outside, target_is_directory=True)
    elif link_kind == "installs":
        (home / "installs").symlink_to(outside, target_is_directory=True)
    else:
        (home / "installs").mkdir()
        (home / "installs" / "linked").symlink_to(outside, target_is_directory=True)
    ownership_runner(home)
    assert (sentinel.stat().st_uid, sentinel.stat().st_gid) == (0, 0)
    assert sentinel.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("installs_exists", [False, True])
def test_install_repair_preserves_unrelated_host_files(
    ownership_runner, tmp_path, installs_exists
):
    home = tmp_path / "home"
    home.mkdir()
    if installs_exists:
        (home / "installs").mkdir()
    unrelated = home / "host-file"
    unrelated.write_text("untouched")
    root_owned(unrelated)
    ownership_runner(home)
    assert unrelated.stat().st_uid == 0
    assert (home / "installs").exists() == installs_exists
