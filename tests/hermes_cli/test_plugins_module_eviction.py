"""Plugin reload eviction survives a real concurrent import on the same registry."""
from __future__ import annotations

import importlib
import sys
import threading
import uuid

from hermes_cli.plugins_loader import _evict_modules


def test_plugin_eviction_preserves_other_modules_during_concurrent_import(monkeypatch, tmp_path):
    prefix = "hermes_eviction_" + uuid.uuid4().hex
    package = tmp_path / prefix
    package.mkdir()
    (package / "__init__.py").write_text("value = 'plugin'\n", encoding="utf-8")
    (package / "child.py").write_text("value = 'child'\n", encoding="utf-8")
    unrelated = prefix + "_unrelated"
    concurrent = prefix + "_concurrent"
    for name in (unrelated, concurrent):
        (tmp_path / f"{name}.py").write_text("value = 'independent'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module(prefix + ".child")
    neighbour = importlib.import_module(unrelated)
    scanning = threading.Event()
    imported = threading.Event()
    failures = []

    class PausingName(str):
        """Pause the first name comparison while another thread performs a real import."""
        paused = False
        __hash__ = str.__hash__

        def __eq__(self, other):
            if not self.paused:
                self.paused = True
                scanning.set()
                assert imported.wait(5), "concurrent import did not finish"
            return super().__eq__(other)

    def import_during_scan():
        try:
            assert scanning.wait(5), "eviction did not begin comparing names"
            importlib.import_module(concurrent)
        except BaseException as exc:
            failures.append(exc)
        finally:
            imported.set()

    worker = threading.Thread(target=import_during_scan)
    worker.start()
    try:
        _evict_modules(PausingName(prefix))
        assert not failures, failures
        assert prefix not in sys.modules
        assert prefix + ".child" not in sys.modules
        assert sys.modules[unrelated] is neighbour
        assert sys.modules[concurrent].value == "independent"
    finally:
        scanning.set()
        worker.join(5)
        for name in sys.modules.copy():
            if name.startswith(prefix):
                sys.modules.pop(name, None)
    assert not worker.is_alive()
