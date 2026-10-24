import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NANOID_V3_SAFE_FLOOR = (3, 3, 18)


def _semver_core(version: str) -> tuple[int, int, int]:
    core = version.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _is_at_least_stable(version: str, floor: tuple[int, int, int]) -> bool:
    without_build = version.split("+", 1)[0]
    _, separator, _ = without_build.partition("-")
    return not separator and _semver_core(without_build) >= floor


def test_nanoid_security_floor_rejects_prerelease_at_fixed_version() -> None:
    assert not _is_at_least_stable("3.3.18-rc.1", NANOID_V3_SAFE_FLOOR)


def test_nanoid_v3_resolutions_are_outside_ghsa_2v37_7h3g_55p8() -> None:
    package = json.loads((ROOT / "package.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())

    override = package["overrides"]["nanoid@^3"]
    assert _is_at_least_stable(override, NANOID_V3_SAFE_FLOOR)

    vulnerable = {
        location: metadata["version"]
        for location, metadata in lock["packages"].items()
        if location.endswith("node_modules/nanoid")
        and _semver_core(metadata["version"])[0] == 3
        and not _is_at_least_stable(metadata["version"], NANOID_V3_SAFE_FLOOR)
    }
    assert vulnerable == {}


def test_nix_node_gyp_lock_omits_unused_development_dependencies() -> None:
    lock = json.loads((ROOT / "nix/node-gyp-11-4-0-package-lock.json").read_text())
    root_package = lock["packages"][""]

    assert "devDependencies" not in root_package
    assert all(not metadata.get("dev") for metadata in lock["packages"].values())

    derivation = (ROOT / "nix/node-gyp-11-4-0.nix").read_text()
    assert "substituteInPlace package.json --replace-fail" in derivation
    assert '"devDependencies"' in derivation
    assert "delete pkg.devDependencies" not in derivation
