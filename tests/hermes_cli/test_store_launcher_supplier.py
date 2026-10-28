"""Regression for PM's pip-free store supplier; stdlib unittest entrypoint."""
from __future__ import annotations

import builtins
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from hermes_cli import _launchers


class StoreLauncherSupplierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        supplied = os.environ.get("HERMES_TEST_DISTLIB")
        if supplied:
            self.supplier = Path(supplied)
        else:
            spec = importlib.util.find_spec("distlib")
            if spec is None:
                try:
                    spec = importlib.util.find_spec("pip._vendor.distlib")
                except ModuleNotFoundError:
                    spec = None
            if spec is None or spec.origin is None:
                self.skipTest("requires the existing pinned distlib test supplier")
            self.supplier = Path(spec.origin).parent
        self.python = self.root / "store" / "python.exe"
        self.python.parent.mkdir()
        self.python.touch()
        self.out = self.root / "bin"
        self.out.mkdir()

    def install_supplier(self, legacy=False):
        site = self.python.parent / "Lib" / "site-packages"
        package = site / "pip" / "_vendor" / "distlib" if legacy else site / "distlib"
        shutil.copytree(self.supplier, package)
        return package

    def test_private_loader_reads_promoted_supplier_without_polluting_imports(self):
        self.install_supplier()
        before = {name: module for name, module in sys.modules.items()
                  if name == "pip" or name.startswith("pip.") or name == "distlib" or name.startswith("distlib.")}
        maker = _launchers._store_script_maker(self.python)
        self.assertIsNotNone(maker)
        self.assertTrue(maker.__module__.startswith("_hermes_store_distlib_"))
        self.assertEqual(before, {name: module for name, module in sys.modules.items()
                                 if name == "pip" or name.startswith("pip.") or name == "distlib" or name.startswith("distlib.")})

    def test_legacy_store_supplier_remains_supported(self):
        self.install_supplier(legacy=True)
        self.assertIsNotNone(_launchers._store_script_maker(self.python))

    def test_repin_mints_native_exe_when_active_environment_has_no_maker(self):
        package = self.install_supplier()
        # A genuine distlib PE/ZIP writer. On POSIX only adapt the platform
        # probe and PE resource accessor, never the writer or returned output.
        maker = _launchers._store_script_maker(self.python)
        adapted = maker
        if maker is not None and os.name != "nt":
            class NativeMaker(maker):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._is_nt = True

                def _get_launcher(self, kind):
                    return (package / f"{kind}64.exe").read_bytes()
            adapted = NativeMaker
        stale = self.out / "hermes.exe"
        stale.write_bytes(b"old launcher bound to the previous store")
        # Fault is the active generation's missing distlib/pip; the real
        # private loader must still discover PM's promoted store copy.
        real_import = builtins.__import__

        def without_active_maker(name, *args, **kwargs):
            if name in ("distlib.scripts", "pip._vendor.distlib.scripts"):
                raise ModuleNotFoundError(f"No module named '{name}'", name=name)
            return real_import(name, *args, **kwargs)

        with patch.object(_launchers, "_is_windows", return_value=True), \
             patch.object(_launchers, "resolve_store_python", return_value=self.python), \
             patch("builtins.__import__", side_effect=without_active_maker):
            self.assertIsNone(_launchers._load_script_maker())
            if adapted is not maker:
                with patch.object(_launchers, "_store_script_maker", return_value=adapted):
                    result = _launchers.stage_launcher("hermes", self.root, self.out)
            else:
                result = _launchers.stage_launcher("hermes", self.root, self.out)
        self.assertEqual(result, stale)
        self.assertFalse((self.out / "hermes.cmd").exists())
        data = stale.read_bytes()
        self.assertEqual(data[:2], b"MZ")
        pe = struct.unpack_from("<I", data, 0x3c)[0]
        self.assertEqual(data[pe:pe + 4], b"PE\0\0")
        self.assertEqual(_launchers._launcher_python(stale), self.python)
        with ZipFile(stale) as archive:
            self.assertEqual(archive.namelist(), ["__main__.py"])
            self.assertEqual(archive.read("__main__.py"),
                             _launchers._launcher_script("hermes", self.root, None).encode())

    def test_rejects_supplier_directory_symlink_escape(self):
        package = self.install_supplier()
        outside = self.root / "outside"
        package.rename(outside)
        package.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(_launchers._store_script_maker(self.python))

    def test_rejects_supplier_file_symlink_escape(self):
        package = self.install_supplier()
        outside = self.root / "outside.py"
        (package / "scripts.py").rename(outside)
        (package / "scripts.py").symlink_to(outside)
        self.assertIsNone(_launchers._store_script_maker(self.python))

    def test_rejects_foreign_private_cached_package(self):
        import hashlib
        import types
        package = self.install_supplier()
        name = "_hermes_store_distlib_" + hashlib.sha256(str(package).encode()).hexdigest()[:12]
        foreign = types.ModuleType(name)
        foreign.__path__ = [str(self.root / "foreign")]
        scripts = types.ModuleType(name + ".scripts")
        scripts.ScriptMaker = object()
        with patch.dict(sys.modules, {name: foreign, name + ".scripts": scripts}):
            self.assertIsNone(_launchers._store_script_maker(self.python))

    def test_rejects_foreign_cached_scripts_after_valid_load(self):
        import types
        self.install_supplier()
        maker = _launchers._store_script_maker(self.python)
        foreign = types.ModuleType(maker.__module__)
        foreign.ScriptMaker = object()
        with patch.dict(sys.modules, {maker.__module__: foreign}):
            self.assertIsNone(_launchers._store_script_maker(self.python))

    def test_runtime_error_supplier_returns_fallback_and_cleans_private_modules(self):
        import hashlib
        package = self.install_supplier()
        name = "_hermes_store_distlib_" + hashlib.sha256(str(package).encode()).hexdigest()[:12]
        (package / "__init__.py").write_text(
            "from . import partial\nraise RuntimeError('torn supplier')\n")
        (package / "partial.py").write_text("loaded = True\n")
        self.assertIsNone(_launchers._store_script_maker(self.python))
        self.assertFalse(any(key == name or key.startswith(name + ".") for key in sys.modules))

    def test_supplier_keyboard_interrupt_is_not_swallowed(self):
        package = self.install_supplier()
        (package / "__init__.py").write_text("raise KeyboardInterrupt()\n")
        with self.assertRaises(KeyboardInterrupt):
            _launchers._store_script_maker(self.python)

    @unittest.skipUnless(os.name == "nt", "native Windows process execution")
    def test_promoted_supplier_exe_executes_on_windows(self):
        self.install_supplier()
        maker = _launchers._store_script_maker(self.python)
        self.assertIsNotNone(maker)
        with patch.object(_launchers, "_load_script_maker", return_value=None), \
             patch.object(_launchers, "_store_script_maker", return_value=maker), \
             patch.object(_launchers, "_launcher_script", return_value="raise SystemExit(7)\n"):
            result = _launchers.mint_launcher("hermes", self.root, self.out, Path(sys.executable), None)
        self.assertEqual(result, self.out / "hermes.exe")
        self.assertEqual(subprocess.run([str(result)], timeout=30, check=False).returncode, 7)


# Keep the stdlib entrypoint usable without pytest while exposing the native
# process case to the marked-OS lane's file discovery and -m selector.
try:
    import pytest
except ImportError:
    pass
else:
    StoreLauncherSupplierTests.test_promoted_supplier_exe_executes_on_windows = (
        pytest.mark.platforms("windows")(
            StoreLauncherSupplierTests.test_promoted_supplier_exe_executes_on_windows
        )
    )


if __name__ == "__main__":
    unittest.main()
