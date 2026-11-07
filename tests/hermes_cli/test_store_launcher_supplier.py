"""A rejected private launcher supplier must not poison the next launcher attempt.

Extracted from PR446, adapted to PM's current authenticated standalone supplier.
Loader cases use real Python imports on the host; native PE execution runs on Windows.
"""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

from hermes_cli import _launchers
from tests.pm._fixtures import host_distlib


@pytest.fixture
def supplier(tmp_path):
    python = tmp_path / "store" / "python.exe"
    python.parent.mkdir()
    python.touch()
    package = python.parent / "Lib" / "site-packages" / "distlib"
    shutil.copytree(host_distlib(), package, ignore=shutil.ignore_patterns("__pycache__"))
    name = "_hermes_store_distlib_" + hashlib.sha256(str(package).encode()).hexdigest()[:12]
    yield python, package, name
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            sys.modules.pop(key, None)


@pytest.mark.parametrize("fault", ["RuntimeError", "KeyboardInterrupt", "SystemExit"])
def test_failed_supplier_import_discards_children_and_preserves_cancellation(supplier, fault):
    python, package, name = supplier
    original = (package / "__init__.py").read_text(encoding="utf-8")
    with (package / "__init__.py").open("a", encoding="utf-8") as stream:
        stream.write(f"\nfrom . import partial\nraise {fault}('interrupted supplier')\n")
    (package / "partial.py").write_text("loaded = True\n", encoding="utf-8")
    if fault == "RuntimeError":
        assert _launchers._store_script_maker(python) is None
    else:
        with pytest.raises({"KeyboardInterrupt": KeyboardInterrupt, "SystemExit": SystemExit}[fault]):
            _launchers._store_script_maker(python)
    assert not any(key == name or key.startswith(name + ".") for key in sys.modules)
    (package / "__init__.py").write_text(original, encoding="utf-8")
    assert isinstance(_launchers._store_script_maker(python), type)


@pytest.mark.parametrize("damage", ["none", "export", "cached-module"])
def test_supplier_rejects_invalid_exports_and_cached_modules(supplier, monkeypatch, damage):
    python, package, name = supplier
    if damage == "export":
        with (package / "scripts.py").open("a", encoding="utf-8") as stream:
            stream.write("\nScriptMaker = 1\n")
    elif damage == "cached-module":
        assert isinstance(_launchers._store_script_maker(python), type)
        foreign = types.ModuleType(name + ".scripts")
        foreign.ScriptMaker = object()
        monkeypatch.setitem(sys.modules, name + ".scripts", foreign)
    before = {key: value for key, value in sys.modules.items()
              if key == "distlib" or key.startswith("distlib.") or key == "pip" or key.startswith("pip.")}
    maker = _launchers._store_script_maker(python)
    if damage == "none":
        assert isinstance(maker, type)
        assert maker.__module__ == name + ".scripts"
    else:
        assert maker is None
        assert not any(key == name or key.startswith(name + ".") for key in sys.modules)
    assert before == {key: value for key, value in sys.modules.items()
                      if key == "distlib" or key.startswith("distlib.") or key == "pip" or key.startswith("pip.")}


@pytest.mark.platforms("posix")
def test_supplier_checks_nested_directory_links_before_import(supplier, tmp_path):
    python, package, _ = supplier
    nested = python.parent / "linked-dependency"
    nested.mkdir()
    sentinel = tmp_path / "outside-executed"
    outside = tmp_path / "outside.py"
    outside.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n", encoding="utf-8")
    (nested / "__init__.py").symlink_to(outside)
    (package / "dependency").symlink_to(nested, target_is_directory=True)
    with (package / "__init__.py").open("a", encoding="utf-8") as stream:
        stream.write("\nfrom . import dependency\n")
    assert _launchers._store_script_maker(python) is None
    assert not sentinel.exists(), "outside-store module ran before the loader refused it"


@pytest.mark.platforms("windows")
def test_promoted_supplier_native_launcher_executes(supplier, tmp_path, monkeypatch):
    python, _, _ = supplier
    maker = _launchers._store_script_maker(python)
    assert isinstance(maker, type)
    monkeypatch.setattr(_launchers, "_store_script_maker", lambda _: maker)
    monkeypatch.setattr(_launchers, "_launcher_script", lambda *args: "raise SystemExit(7)\n")
    output = tmp_path / "bin"
    output.mkdir()
    launcher = _launchers.mint_launcher("hermes", tmp_path, output, Path(sys.executable), None)
    assert launcher == output / "hermes.exe"
    assert subprocess.run([str(launcher)], check=False, timeout=30).returncode == 7
