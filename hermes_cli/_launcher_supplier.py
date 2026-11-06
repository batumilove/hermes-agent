"""Private imports of PM's authenticated standalone native-launcher supplier."""

from pathlib import Path
import sys


def _check_package(package: Path, entry: Path) -> None:
    from pm.python_bootstrap import _checked_distlib

    _checked_distlib(package, entry)
    store = entry.resolve(strict=True)
    for resource in ("__init__.py", "scripts.py", "LICENSE.txt", "resources.py", "t32.exe", "t64.exe", "t64-arm.exe"):
        path = package / resource
        if (not path.is_file() or not path.resolve().is_relative_to(store)
                or (resource.endswith(".exe") and not path.read_bytes().startswith(b"MZ"))):
            raise ValueError(f"invalid native launcher resource: {resource}")
    # Walk resolved directories too: rglob does not descend into directory links.
    pending, visited = [package], set()
    while pending:
        directory = pending.pop().resolve(strict=True)
        if not directory.is_relative_to(store):
            raise ValueError("launcher supplier directory leaves the store")
        if directory in visited:
            continue
        visited.add(directory)
        for path in directory.iterdir():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(store):
                raise ValueError("launcher supplier file leaves the store")
            if resolved.is_dir():
                pending.append(resolved)


def _private_modules(name: str):
    return [(key, module) for key, module in list(sys.modules.items())
            if key == name or key.startswith(name + ".")]


def _check_modules(name: str, package: Path) -> None:
    for key, module in _private_modules(name):
        relative = key[len(name):].lstrip(".").split(".") if key != name else []
        expected = package.joinpath(*relative)
        is_package = hasattr(module, "__path__")
        origin = (expected / "__init__.py" if is_package else expected.with_suffix(".py")).resolve(strict=True)
        spec = getattr(module, "__spec__", None)
        if (spec is None or spec.name != key
                or Path(spec.origin or "").resolve() != origin
                or Path(getattr(module, "__file__", "")).resolve() != origin
                or getattr(module, "__package__", None) != (key if is_package else key.rpartition(".")[0])
                or not origin.is_relative_to(package.resolve())
                or (is_package and list(module.__path__) != [str(expected)])):
            raise ImportError("foreign private distlib module")


def _discard_modules(name: str) -> None:
    for key, _ in _private_modules(name):
        sys.modules.pop(key, None)
