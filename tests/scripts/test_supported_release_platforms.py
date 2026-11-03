"""Live release admission excludes unsupported platforms while preserving historical readers."""

import pytest

from scripts.releases import job_groups, lanes


def test_build_admission_emits_only_supported_groups(monkeypatch, capsys):
    monkeypatch.delenv("JOBS", raising=False)
    job_groups.main()
    outputs = dict(line.split("=") for line in capsys.readouterr().out.splitlines())
    assert outputs["all-jobs"] == "true"
    assert outputs["darwin-arm64"] == outputs["darwin-x64"] == "true"
    assert outputs["linux-x64"] == outputs["linux-arm64"] == "true"
    for group in ("termux", "win32-arm64", "win32-x64", "win32-bundle"):
        monkeypatch.setenv("JOBS", group)
        with pytest.raises(ValueError, match="unsupported platforms"):
            job_groups.main()
        assert outputs[group] == "false"


def test_stable_admission_requires_supported_lanes(tmp_path):
    from scripts.releases.entrypoint import release
    lanes.require_supported_lanes(lanes.DEFAULT)
    assert lanes.targets(lanes.DEFAULT) == {"macos/arm64", "macos/x64"}
    for lane in lanes.UNSUPPORTED_LANES:
        with pytest.raises(ValueError, match="Unsupported release lanes"):
            release("a" * 40, bump="patch", repo=tmp_path / "absent", remote="origin",
                    repository="example/repo", execute=lambda _: pytest.fail("No release side effects"),
                    lanes=lanes.DEFAULT | {lane})


def test_channel_protection_distinguishes_current_and_archived_claims():
    assert lanes.advances_desktop({"macos-arm64", "macos-intel"})
    assert not lanes.advances_desktop({"macos-arm64"})
    assert not lanes.advances_desktop({"macos-intel"})
    historical = {"windows", "macos-arm64"}
    assert lanes.advances_desktop(historical)
    with pytest.raises(ValueError, match="Unsupported release lanes"):
        lanes.require_supported_lanes(historical)
