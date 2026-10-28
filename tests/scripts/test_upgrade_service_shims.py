"""Shared upgrade/Desktop service shims permit discovery, never supervision."""

import subprocess

import pytest

from hermes_cli.update_cmd_fleet import _LIST_GATEWAY_UNITS
from tests.e2e.core.upgrade._helpers import write_shims


@pytest.mark.platforms("posix")
def test_service_shims_allow_only_exact_gateway_listing(tmp_path):
    bin_dir = tmp_path / "bin"
    log = write_shims(bin_dir)
    calls = []
    for scope in ([], ["--user"]):
        argv = scope + _LIST_GATEWAY_UNITS
        result = subprocess.run([str(bin_dir / "systemctl"), *argv], capture_output=True, text=True, check=False)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
        calls.append("systemctl " + " ".join(argv))
    denied = [
        ("systemctl", ["start", "fixture-only.service"]),
        ("systemctl", ["--user", "stop", "fixture-only.service"]),
        ("systemctl", _LIST_GATEWAY_UNITS[:-1]),
        ("systemctl", _LIST_GATEWAY_UNITS + ["--all"]),
        ("systemctl", [" ".join(_LIST_GATEWAY_UNITS)]),
        ("systemctl", ["--user", "--user", *_LIST_GATEWAY_UNITS]),
        ("systemctl", [*_LIST_GATEWAY_UNITS[:-2], "--no-legend --no-pager"]),
        ("systemctl", ["list-units", "unrelated.service", *_LIST_GATEWAY_UNITS[5:]]),
        ("systemctl", []),
        ("launchctl", ["list"]),
        ("sudo", ["true"]),
        ("loginctl", ["list-users"]),
        ("journalctl", ["--user"]),
    ]
    for name, argv in denied:
        result = subprocess.run([str(bin_dir / name), *argv], capture_output=True, text=True, check=False)
        assert (result.returncode, result.stdout, result.stderr) == (
            1, "", "Failed to connect to bus: sandboxed test shim\n"
        )
        calls.append(name + " " + " ".join(argv))
    assert log.read_text().splitlines() == calls
    before = log.read_bytes()
    with pytest.raises(RuntimeError, match="live-system guard: blocked"):
        subprocess.run(
            [str(bin_dir / "systemctl"), "--user", "stop", "hermes-gateway.service"],
            capture_output=True, text=True, check=False,
        )
    assert log.read_bytes() == before  # Real-unit protection rejects before the shim executes.
