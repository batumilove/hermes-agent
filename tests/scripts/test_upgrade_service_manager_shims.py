"""The upgrade sandbox permits only the production read-only gateway listing."""
import subprocess

import pytest

from tests.e2e.core.upgrade._helpers import write_shims


_LISTING = [
    "list-units", "hermes-gateway*", "hermes-*-gateway.service", "hermes-serve*",
    "hermes-dashboard*", "--plain", "--no-legend", "--no-pager",
]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("scope", [[], ["--user"]])
def test_gateway_listing_is_empty_and_successful(tmp_path, scope):
    log = write_shims(tmp_path)
    result = subprocess.run(
        [str(tmp_path / "systemctl"), *scope, *_LISTING],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert log.read_text().splitlines() == [" ".join(["systemctl", *scope, *_LISTING])]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("manager,argv", [
    ("systemctl", ["--user", "restart", "fixture-only.service"]),
    ("systemctl", ["stop", "fixture-only.service"]),
    ("systemctl", _LISTING[:-1]),
    ("systemctl", [*_LISTING, "--all"]),
    ("systemctl", [" ".join(_LISTING)]),
    ("systemctl", ["--system", *_LISTING]),
    ("systemctl", ["--user", "--user", *_LISTING]),
    ("systemctl", ["list-units", "other.service", *_LISTING[2:]]),
    ("systemctl", []),
    ("launchctl", _LISTING),
    ("sudo", ["systemctl", *_LISTING]),
    ("loginctl", _LISTING),
    ("journalctl", _LISTING),
])
def test_mutations_and_near_matches_remain_denied(tmp_path, manager, argv):
    log = write_shims(tmp_path)
    result = subprocess.run(
        [str(tmp_path / manager), *argv], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "sandboxed test shim" in result.stderr
    assert log.read_text().splitlines() == [f"{manager} {' '.join(argv)}"]


@pytest.mark.platforms("posix")
def test_real_gateway_mutation_is_guarded_before_the_shim(tmp_path):
    log = write_shims(tmp_path)
    with pytest.raises(RuntimeError, match="live-system guard: blocked"):
        subprocess.run(
            [str(tmp_path / "systemctl"), "--user", "restart", "hermes-gateway.service"],
            capture_output=True, text=True, check=False,
        )
    assert not log.exists()


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("listing_rc", [0, 1])
def test_production_discovery_preserves_listing_refusal(tmp_path, monkeypatch, listing_rc):
    from hermes_cli import gateway, update_cmd_fleet, update_cmd_posix_pause

    write_shims(tmp_path)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway, "_ensure_user_systemd_env", lambda: None)
    seen = []

    def systemctl(argv, timeout):
        assert argv[0] == "systemctl"
        seen.append(argv[1:])
        result = subprocess.run(
            [str(tmp_path / "systemctl"), *argv[1:]], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        assert result.returncode == 0  # the real generated listing shim is exercised
        return subprocess.CompletedProcess(argv, listing_rc, result.stdout, result.stderr)

    monkeypatch.setattr(update_cmd_fleet, "_systemctl", systemctl)
    if listing_rc:
        with pytest.raises(update_cmd_posix_pause.PauseRefused, match="could not list user gateway units"):
            update_cmd_posix_pause._discover_systemd()
        assert seen == [["--user", *_LISTING]]
    else:
        assert update_cmd_posix_pause._discover_systemd() == []
        assert seen == [["--user", *_LISTING], _LISTING]
