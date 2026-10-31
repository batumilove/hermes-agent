"""Keep modern named-API dependency paths fixed without overriding legacy callers."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_modern_glob_and_minimatch_paths_are_fixed():
    packages = json.loads((ROOT / "package-lock.json").read_text())["packages"]
    assert packages["node_modules/@electron/asar/node_modules/glob"]["version"] == "14.0.3"
    assert packages["node_modules/cacache/node_modules/glob"]["version"] == "14.0.3"
    assert packages["node_modules/minimatch"]["version"] == "11.0.3"
    assert packages["node_modules/get-windows/node_modules/node-gyp/node_modules/glob"]["version"] == "10.5.0"
    for parent in ["eslint", "@eslint/config-array", "@typescript-eslint/typescript-estree", "app-builder-lib"]:
        assert packages[f"node_modules/{parent}/node_modules/minimatch"]["version"] == "10.2.6"
    manifest = json.loads((ROOT / "package.json").read_text())
    assert "glob@>=9" not in manifest["overrides"]
    assert "minimatch@>=9" not in manifest["overrides"]


def test_legacy_callers_are_not_blindly_overridden():
    manifest = json.loads((ROOT / "package.json").read_text())
    assert "glob" not in manifest["overrides"]
    assert "minimatch" not in manifest["overrides"]
    packages = json.loads((ROOT / "package-lock.json").read_text())["packages"]
    assert packages["node_modules/glob"]["version"] == "7.2.3"
    assert packages["node_modules/rimraf"]["version"] == "2.6.3"
    assert packages["node_modules/electron-winstaller/node_modules/@electron/asar"]["version"] == "3.4.1"
