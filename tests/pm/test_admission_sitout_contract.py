"""Existing incompatible members sit out; newly requested members still refuse."""
import pytest

import hermes_cli.plugins_manifest as gate
import pm.plugins_state
from pm.publication import candidate_members


def _plugin(home, name, manifest=""):
    path = home / "plugins" / name
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text(f"name: {name}\n{manifest}")
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-plugin-{name}"\nversion = "1.0.0"\ndependencies = ["httpx>=0.28,<1"]\n')
    return path


@pytest.fixture
def homes(tmp_path, monkeypatch):
    default, profile = tmp_path / "default", tmp_path / "profile"
    good = _plugin(default, "good")
    bad = _plugin(profile, "bad", "requires_hermes: '>=99.0'\n")
    for home, name in [(default, "good"), (profile, "bad")]:
        (home / "config.yaml").write_text(f"plugins:\n  enabled: [{name}]\n")
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [default, profile])
    monkeypatch.setattr(gate, "running_hermes_version", lambda: "0.20.5")
    return default, profile, good, bad


def test_existing_cross_home_member_sits_out(homes, capsys):
    default, profile, good, _bad = homes
    before = (profile / "config.yaml").read_bytes()
    assert candidate_members(proposed_home=default, enabled=["good"], disabled=[]) == [good]
    assert (profile / "config.yaml").read_bytes() == before
    assert "bad" in capsys.readouterr().err


def test_newly_requested_incompatible_member_refuses(homes):
    _default, profile, _good, _bad = homes
    (profile / "config.yaml").write_text("plugins:\n  enabled: []\n")
    with pytest.raises(ValueError, match="requires hermes"):
        candidate_members(proposed_home=profile, enabled=["bad"], disabled=[])


def test_extra_incompatible_member_refuses(homes):
    default, _profile, _good, bad = homes
    with pytest.raises(ValueError, match="requires hermes"):
        candidate_members([bad], proposed_home=default, enabled=["good"], disabled=[])


def test_nonmember_does_not_join(homes):
    _default, _profile, good, _bad = homes
    (good / "pyproject.toml").unlink()
    assert candidate_members() == []


@pytest.mark.parametrize("manifest", ["manifest_version: 99\n", "[not: valid\n",
                                      "requires_hermes: '>=99.0'\nmanifest_version: 99\n"])
def test_other_manifest_errors_refuse(homes, manifest):
    _default, _profile, _good, bad = homes
    (bad / "plugin.yaml").write_text(manifest)
    with pytest.raises(ValueError):
        candidate_members()


def test_invalid_dependency_declaration_still_refuses(homes):
    _default, _profile, _good, bad = homes
    (bad / "pyproject.toml").write_text("not valid toml")
    with pytest.raises(ValueError):
        candidate_members()


def test_member_rejoins_when_version_fits(homes, monkeypatch):
    _default, _profile, good, bad = homes
    monkeypatch.setattr(gate, "running_hermes_version", lambda: "99.0.0")
    assert candidate_members() == [good, bad]
