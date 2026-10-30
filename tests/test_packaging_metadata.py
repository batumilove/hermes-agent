"""Independent core/optional dependency and reviewed CVE policies."""
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_test_dependencies_are_group_only_in_manifest_and_lock():
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    hermes = next(package for package in lock["package"] if package["name"] == manifest["project"]["name"])
    assert manifest["tool"]["uv"]["default-groups"] == []
    assert "dev" in manifest["dependency-groups"]
    assert "dev" not in manifest["project"]["optional-dependencies"]
    assert "dev" in hermes["dev-dependencies"]
    assert "dev" not in hermes.get("optional-dependencies", {})


def test_core_and_optional_speech_dependencies():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = {Requirement(dep).name for dep in project["dependencies"]}
    assert "packaging" in core  # Runtime code imports it directly, not transitively.
    assert "faster-whisper" not in core
    assert "faster-whisper" in {
        Requirement(dep).name for dep in project["optional-dependencies"]["stt-whisper"]
    }


def test_starlette_server_pins_and_lock_exclude_cve_2026_48710():
    # BadHost's reviewed fixed boundary is independent of today's exact pin.
    floor = Version("1.0.1")
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    found = set()
    for extra, specs in metadata["project"]["optional-dependencies"].items():
        for requirement in map(Requirement, specs):
            if requirement.name != "starlette":
                continue
            pins = list(requirement.specifier)
            assert len(pins) == 1 and pins[0].operator == "==", (extra, requirement)
            assert Version(pins[0].version) >= floor, (extra, requirement)
            found.add(extra)
    assert {"web", "mcp", "computer-use"} <= found
    dev = [req for req in map(Requirement, metadata["dependency-groups"]["dev"])
           if req.name == "starlette"]
    assert len(dev) == 1
    pins = list(dev[0].specifier)
    assert len(pins) == 1 and pins[0].operator == "==" and Version(pins[0].version) >= floor
    versions = [Version(row["version"]) for row in lock["package"] if row["name"] == "starlette"]
    assert versions and all(version >= floor for version in versions)


CA_TRUST_PACKAGES = {"certifi"}


def test_release_age_preserves_ca_trust_and_reviewed_security_exception():
    """Keep the global cooldown and bound the approved decoder exception."""
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    uv = manifest["tool"]["uv"]
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert uv["exclude-newer"] == "3 days"
    exempt = uv.get("exclude-newer-package", {})
    assert set(exempt) <= CA_TRUST_PACKAGES | {"brotlicffi"}, exempt
    assert all(value is False for name, value in exempt.items() if name in CA_TRUST_PACKAGES), exempt
    if "brotlicffi" in exempt:
        assert exempt["brotlicffi"] == "2026-10-23T00:00:00Z"
        assert {row["version"] for row in lock["package"] if row["name"] == "brotlicffi"} == {"1.2.1.0"}
    assert lock["options"].get("exclude-newer-span") == "P3D"
    assert lock["options"].get("exclude-newer-package", {}) == exempt


def test_messaging_decoder_excludes_pysec_2026_4323():
    """The advisory's fixed boundary remains valid after future pin updates."""
    floor = Version("1.2.1.0")
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = [req for req in map(Requirement, manifest["project"]["optional-dependencies"]["messaging"])
             if req.name == "brotlicffi"]
    assert specs
    for req in specs:
        pins = list(req.specifier)
        assert len(pins) == 1 and pins[0].operator == "=="
        assert Version(pins[0].version) >= floor
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [Version(row["version"]) for row in lock["package"] if row["name"] == "brotlicffi"]
    assert versions and all(version >= floor for version in versions)


def test_httpx2_pins_and_lock_exclude_ghsa_7mj9_2mp8_4m2p_family():
    # 2.12.0 is the first httpx2/httpcore2 release clear of GHSA-7mj9-2mp8-4m2p,
    # GHSA-f2fp-rgf2-35cp, GHSA-h4x7-gw46-3wm6, GHSA-pf96-p4fj-6566 and
    # GHSA-8xx6-hgc6-gc2m; the boundary is independent of today's exact pin.
    floor = Version("2.12.0")
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    declared = dict(metadata["project"]["optional-dependencies"])
    declared["group:dev"] = metadata["dependency-groups"]["dev"]
    found = set()
    for where, specs in declared.items():
        for requirement in map(Requirement, specs):
            if requirement.name != "httpx2":
                continue
            pins = list(requirement.specifier)
            assert len(pins) == 1 and pins[0].operator == "==", (where, requirement)
            assert Version(pins[0].version) >= floor, (where, requirement)
            found.add(where)
    assert {"mcp", "computer-use", "group:dev"} <= found
    for name in ("httpx2", "httpcore2"):
        versions = [Version(row["version"]) for row in lock["package"] if row["name"] == name]
        assert versions and all(version >= floor for version in versions), (name, versions)


def test_build_and_lock_helpers_exclude_reviewed_advisories():
    """Fixed boundaries for filelock permissions and setuptools archive handling."""
    floors = {"filelock": Version("4.2.1"), "setuptools": Version("84.1.0")}
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    for name, floor in floors.items():
        versions = [Version(row["version"]) for row in lock["package"] if row["name"] == name]
        assert versions and all(version >= floor for version in versions), (name, versions)
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(spec) for spec in manifest["build-system"]["requires"]]
    setuptools = next(req for req in requirements if req.name == "setuptools")
    pins = list(setuptools.specifier)
    assert len(pins) == 1 and pins[0].operator == "=="
    assert Version(pins[0].version) >= floors["setuptools"]


def test_tornado_constraint_and_lock_exclude_october_2026_advisories():
    """GHSA-pcg2-g6cj-q92c / pw9p-v7r6-gx4p / r4j5-9xpp-3985."""
    floor = Version("6.6")
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(spec) for spec in manifest["tool"]["uv"]["constraint-dependencies"]]
    requirement = next(req for req in requirements if req.name == "tornado")
    assert floor in requirement.specifier
    assert Version("6.5.10") not in requirement.specifier
    assert Version("7") not in requirement.specifier
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [Version(row["version"]) for row in lock["package"] if row["name"] == "tornado"]
    assert versions and all(version >= floor and version in requirement.specifier for version in versions)
