"""PM Python retains launcher helpers without shipping a second package installer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pm.packages import Python
from pm.store import current_target
from pm.python_bootstrap import DISTLIB_VERSION, _distlib_version


def host_distlib() -> Path:
    """Use the existing test toolchain supplier matching PM's archive contract.

    A venv's base Python can carry older pip than the test venv. Do not copy
    that 0.3.8 supplier into a fixture asserting the pinned 0.4.2 contract.
    No package is installed and the production version gate stays unchanged.
    """
    for base in dict.fromkeys((Path(sys.prefix), Path(sys.base_prefix))):
        for site in (base / "Lib" / "site-packages", *base.glob("lib/python*/site-packages")):
            for candidate in (site / "distlib", site / "pip" / "_vendor" / "distlib"):
                if (candidate / "scripts.py").is_file() and _distlib_version(candidate) == DISTLIB_VERSION:
                    return candidate
    raise AssertionError(f"test toolchain requires existing distlib {DISTLIB_VERSION}")


@pytest.fixture
def bootstrap_tree(tmp_path):
    root = tmp_path / "python"
    library = root / "Lib" if os.name == "nt" else root / "lib" / "python3.14"
    site = library / "site-packages"
    vendor = site / "pip" / "_vendor"
    vendor.mkdir(parents=True)
    shutil.copytree(host_distlib(), vendor / "distlib", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("msgpack", "urllib3", "pkg_resources"):
        (vendor / name).mkdir()
        (vendor / name / "__init__.py").write_text("# old bundled supplier\n", encoding="utf-8")
    (site / "pip-26.2.1.dist-info").mkdir()
    bundle = library / "ensurepip" / "_bundled"
    bundle.mkdir(parents=True)
    (bundle / "pip-26.2.1-py3-none-any.whl").write_bytes(b"archive bootstrap payload")
    stdlib = bundle.parent / "__init__.py"
    stdlib.write_text("# stdlib module retained\n", encoding="utf-8")
    binary_dir = root / ("Scripts" if os.name == "nt" else "bin")
    binary_dir.mkdir()
    executable = root / "python.exe" if os.name == "nt" else binary_dir / "python3"
    if sys.platform == "linux":
        executable.symlink_to(Path(sys.executable).resolve())
    else:
        # Darwin's staging signer must receive a private copy, not the live tool.
        shutil.copy2(Path(sys.executable).resolve(), executable)
    for name in (("pip.exe", "pip3.exe", "pip3.14.exe") if os.name == "nt" else ("pip", "pip3", "pip3.14")):
        (binary_dir / name).write_text("old pip launcher\n", encoding="utf-8")
    (binary_dir / "pip-tools").write_text("unrelated command\n", encoding="utf-8")
    return root, site, bundle


def test_cached_python_with_bootstrap_pip_is_rejected_without_mutation(bootstrap_tree):
    root, site, bundle = bootstrap_tree
    reason = Python().verify(root, current_target())
    assert reason and "pip" in reason
    assert (site / "pip").is_dir()
    assert list(bundle.glob("*.whl")), "read-only verification mutated cached bytes"


def test_staging_preserves_native_launchers_and_unseeded_venv(bootstrap_tree, tmp_path):
    root, site, bundle = bootstrap_tree
    package = Python()
    package.stage(None, root, "3.14.7+20260901", current_target())
    assert not (site / "pip").exists()
    assert not list(site.glob("pip-*.dist-info"))
    assert not list(bundle.glob("*.whl"))
    binary_dir = root / ("Scripts" if os.name == "nt" else "bin")
    assert not (binary_dir / ("pip.exe" if os.name == "nt" else "pip")).exists()
    assert (binary_dir / "pip-tools").is_file()
    assert (bundle.parent / "__init__.py").read_text(encoding="utf-8") == "# stdlib module retained\n"
    assert (site / "distlib" / "LICENSE.txt").is_file()
    assert package.verify(root, current_target()) == ""
    program = """
import importlib.util, json, os, sys, venv
sys.path.insert(0, sys.argv[1])
from distlib.scripts import ScriptMaker
assert importlib.util.find_spec('pip') is None
maker = ScriptMaker(None, sys.argv[2])
maker.executable = sys.executable
maker.set_mode = True
scripts = maker.make('launcher = sys:exit')
venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(sys.argv[3])
print(json.dumps({'scripts': scripts, 'distlib': __import__('distlib').__version__}))
"""
    launchers, environment = tmp_path / "launchers", tmp_path / "environment"
    result = subprocess.run([sys.executable, "-I", "-S", "-c", program, str(site),
                             str(launchers), str(environment)], check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    receipt = json.loads(result.stdout)
    assert receipt["distlib"] == "0.4.2"
    assert receipt["scripts"] and all(Path(path).is_file() for path in receipt["scripts"])
    subprocess.run([receipt["scripts"][0]], check=True, capture_output=True, timeout=30)
    assert (environment / "pyvenv.cfg").is_file()
    assert not list(environment.rglob("pip-*.dist-info"))


def test_staging_rejects_missing_launcher_supplier_before_removing_pip(bootstrap_tree):
    root, site, _bundle = bootstrap_tree
    shutil.rmtree(site / "pip" / "_vendor" / "distlib")
    with pytest.raises(ValueError, match="distlib"):
        Python().stage(None, root, "3.14.7+20260901", current_target())
    assert (site / "pip").exists()


def test_staging_rejects_conflicting_standalone_distlib(bootstrap_tree):
    root, site, _bundle = bootstrap_tree
    shutil.copytree(site / "pip" / "_vendor" / "distlib", site / "distlib")
    (site / "distlib" / "scripts.py").write_text("# conflicting launcher implementation\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        Python().stage(None, root, "3.14.7+20260901", current_target())
    assert (site / "pip").exists()


def test_cached_python_cannot_reintroduce_ensurepip_payload(bootstrap_tree):
    root, _site, bundle = bootstrap_tree
    package = Python()
    package.stage(None, root, "3.14.7+20260901", current_target())
    assert package.verify(root, current_target()) == ""
    wheel = bundle / "pip-26.2.1-py3-none-any.whl"
    wheel.write_bytes(b"reintroduced bootstrap")
    assert "pip/bootstrap" in package.verify(root, current_target())
    assert wheel.read_bytes() == b"reintroduced bootstrap"


@pytest.mark.platforms("posix")
def test_library_parent_alias_does_not_add_a_launcher_supplier(bootstrap_tree):
    from pm.store import tree_digest

    root, site, _bundle = bootstrap_tree
    # Case-insensitive volumes already alias Lib to lib; reproduce the same
    # parent-directory layout explicitly on a case-sensitive POSIX volume.
    alias = root / "Lib"
    if not alias.exists():
        alias.symlink_to("lib", target_is_directory=True)
    assert alias.samefile(root / "lib")
    assert not (alias / "site-packages" / "distlib").exists()
    package = Python()
    package.stage(None, root, "3.14.7+20260901", current_target())
    assert (site / "distlib" / "scripts.py").is_file()
    digest = tree_digest(root)
    assert package.verify(root, current_target()) == ""
    assert tree_digest(root) == digest, "verification changed the staged tree"


@pytest.mark.parametrize("damage", ["second_supplier", "malformed_supplier"])
def test_cached_python_rejects_invalid_launcher_suppliers(bootstrap_tree, damage):
    from pm.store import tree_digest

    root, site, _bundle = bootstrap_tree
    package = Python()
    package.stage(None, root, "3.14.7+20260901", current_target())
    if damage == "second_supplier":
        other = root / "lib" / "python3.13" / "site-packages" / "distlib"
        other.parent.mkdir(parents=True)
        shutil.copytree(site / "distlib", other)
        expected = "unambiguous"
    else:
        shutil.rmtree(site / "distlib")
        (site / "distlib").write_bytes(b"not a launcher package")
        expected = "invalid"
    digest = tree_digest(root)
    assert expected in package.verify(root, current_target())
    assert tree_digest(root) == digest, "verification changed an invalid supplier"


def test_deb_prefix_is_sanitized_without_executing_foreign_python(bootstrap_tree, tmp_path):
    from pm.python_bootstrap import python_bootstrap_problem

    root, _site, _bundle = bootstrap_tree
    staged = tmp_path / "deb"
    prefix = staged / Python.prefix_rel
    prefix.parent.mkdir(parents=True)
    shutil.move(root, prefix)
    Python().stage(None, staged, "3.14.6-1", "linux-arm64-bionic")
    assert python_bootstrap_problem(prefix, require_distlib=False) == ""
    assert not list(prefix.rglob("pip-*.whl"))
    assert list(prefix.rglob("distlib/scripts.py"))
