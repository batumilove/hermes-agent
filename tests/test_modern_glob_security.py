"""Keep modern named-API dependency paths fixed without overriding legacy callers."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_modern_glob_and_minimatch_paths_are_fixed():
    packages = json.loads((ROOT / "package-lock.json").read_text())["packages"]
    for path, entry in packages.items():
        if path.endswith("node_modules/glob") and int(entry["version"].split(".")[0]) >= 9:
            assert entry["version"] == "14.0.3", (path, entry["version"])
        if path.endswith("node_modules/minimatch") and int(entry["version"].split(".")[0]) >= 9:
            assert entry["version"] == "11.0.3", (path, entry["version"])


def test_legacy_callers_are_not_blindly_overridden():
    manifest = json.loads((ROOT / "package.json").read_text())
    assert "glob" not in manifest["overrides"]
    assert "minimatch" not in manifest["overrides"]
    packages = json.loads((ROOT / "package-lock.json").read_text())["packages"]
    assert packages["node_modules/glob"]["version"] == "7.2.3"
    assert packages["node_modules/rimraf"]["version"] == "2.6.3"
    assert packages["node_modules/electron-winstaller/node_modules/@electron/asar"]["version"] == "3.4.1"
