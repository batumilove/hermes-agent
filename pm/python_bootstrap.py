"""Prepare PM's Python without pip's separately bundled dependency graph.

PM uses its pinned UV for package operations and unseeded environments. Keep
the archive's reviewed distlib launcher helper, including Windows PE stubs and
license, as a standalone module before removing the unused pip/bootstrap copy.
All transformations precede store publication and its recorded tree digest.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil

DISTLIB_VERSION = "0.4.2"  # The distlib shipped by the pinned Python archive's pip 26.2.1.
_PIP_LAUNCHER = re.compile(r"pip(?:\d+(?:\.\d+)?)?(?:\.exe|-script\.py)?$")


def _libraries(entry: Path) -> list[Path]:
    return [directory for directory in (entry / "Lib", *entry.glob("lib/python*")) if directory.is_dir()]


def _distlib_version(directory: Path) -> str | None:
    tree = ast.parse((directory / "__init__.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)):
            return node.value.value if isinstance(node.value.value, str) else None
    return None


def _checked_distlib(directory: Path, entry: Path) -> None:
    if not directory.resolve().is_relative_to(entry.resolve()):
        raise ValueError("distlib supplier is outside staged Python")
    if (not (directory / "__init__.py").is_file()
            or not (directory / "scripts.py").is_file()
            or not (directory / "LICENSE.txt").is_file()
            or _distlib_version(directory) != DISTLIB_VERSION):
        raise ValueError("unexpected distlib supplier in staged Python")


def _bootstrap_paths(entry: Path) -> list[Path]:
    paths = []
    for library in _libraries(entry):
        site = library / "site-packages"
        paths.extend(path for path in (site / "pip", *site.glob("pip-*.dist-info")) if path.exists())
        paths.extend((library / "ensurepip" / "_bundled").glob("*.whl"))
    for directory in (entry / "bin", entry / "Scripts"):
        if directory.is_dir():
            paths.extend(path for path in directory.iterdir() if _PIP_LAUNCHER.fullmatch(path.name))
    return paths


def prepare_python_bootstrap(entry: Path) -> None:
    """Promote launcher helpers, then remove pip payloads; no interpreter executes."""
    suppliers = []
    for library in _libraries(entry):
        if not library.resolve().is_relative_to(entry.resolve()):
            raise ValueError("Python library is outside staged entry")
        site = library / "site-packages"
        pip = site / "pip"
        if not pip.exists():
            continue
        vendor, standalone = pip / "_vendor" / "distlib", site / "distlib"
        _checked_distlib(vendor, entry)
        if standalone.exists():
            _checked_distlib(standalone, entry)
            from pm.store import tree_digest

            if tree_digest(standalone) != tree_digest(vendor):
                raise ValueError("standalone distlib differs from the authenticated supplier")
        suppliers.append((vendor, standalone))
    # Authenticate all suppliers before any bootstrap payload is removed.
    for vendor, standalone in suppliers:
        if not standalone.exists():
            shutil.move(str(vendor), standalone)
    for path in _bootstrap_paths(entry):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def python_bootstrap_problem(entry: Path, *, require_distlib: bool) -> str:
    """Read-only guard also rejects old cached entries with a second installer."""
    leftover = _bootstrap_paths(entry)
    if leftover:
        return f"Python still contains pip/bootstrap payload: {leftover[0].relative_to(entry)}"
    if not require_distlib:
        return ""
    try:
        suppliers = [supplier for library in _libraries(entry)
                     if (supplier := library / "site-packages" / "distlib").exists()]
        if len(suppliers) != 1:
            return "Python has no unambiguous standalone distlib launcher supplier"
        _checked_distlib(suppliers[0], entry)
    except (OSError, ValueError, SyntaxError) as exc:
        return f"Python standalone distlib is invalid: {exc}"
    return ""
