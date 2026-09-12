"""Protect a running profile's SQLite session store from ad-hoc clients.

The gateway owns ``HERMES_HOME/state.db`` while it is running.  A second SQLite
runtime can otherwise reset or reinterpret the hot WAL/SHM pair.  This guard is
intentionally narrow: local terminal commands that launch the sqlite3 CLI or a
Python interpreter and reference that exact live database are blocked.  The
supported inspection path is ``hermes state-db query``, which runs under the
same Hermes Python runtime and opens the database read-only.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
from pathlib import Path
from typing import Callable


_BLOCK_MESSAGE = (
    "live state.db access from an ad-hoc SQLite runtime; use "
    "`hermes state-db query --sql 'SELECT ...'` for read-only inspection, "
    "or stop the gateway before maintenance"
)
_PYTHON_NAME_RE = re.compile(
    r"py(?:\.exe)?|python[23]?(?:\.\d+)*(?:\.exe)?|pypy[23]?(?:\.exe)?",
    re.I,
)
_SQLITE_NAME_RE = re.compile(r"sqlite3(?:\.exe)?", re.I)
_SCRIPT_RUNNER_NAMES = frozenset({"bash", "sh", "dash", "zsh", "ksh"})


def gateway_is_live(hermes_home: Path) -> bool:
    """Return whether this process or the shared status layer sees a live gateway."""
    try:
        from tools.process_registry import _is_supervised_gateway_process

        if _is_supervised_gateway_process():
            return True
    except Exception:
        pass

    try:
        from gateway.status import resolve_gateway_liveness

        liveness = resolve_gateway_liveness(
            profile_dir=hermes_home,
            use_cache=False,
        )
        # An unreadable identity source is unknown, not evidence that the
        # gateway is down.  Maintenance remains available when every probe
        # cleanly reports down (probe_error=False).
        return liveness.running or liveness.probe_error
    except Exception:
        return True


def _command_executables(command: str) -> set[str]:
    """Return executable basenames at shell command positions and in -c payloads."""
    try:
        from tools.approval import (
            _command_detection_variants,
            _deobfuscate_shell_word_for_detection,
            _iter_shell_command_word_spans,
        )

        names: set[str] = set()
        for variant in _command_detection_variants(command):
            for _start, _end, word in _iter_shell_command_word_spans(variant):
                decoded = _deobfuscate_shell_word_for_detection(word)
                names.add(os.path.basename(decoded).lower())
        return names
    except Exception:
        # Fail narrow rather than treating prose arguments as executables.  The
        # normal command parser has its own unconditional malformed-input floor.
        return set()


def _expanded_command(command: str, hermes_home: Path) -> str:
    home = str(hermes_home)
    expanded = command
    for marker in ("${HERMES_HOME}", "${HERMES_HOME%/}", "$HERMES_HOME"):
        expanded = expanded.replace(marker, home)
    expanded = expanded.replace("~/.hermes", home)
    return expanded


def _references_target(command: str, target: Path, cwd: Path) -> bool:
    expanded = _expanded_command(command, target.parent)
    target_norm = os.path.normcase(str(target.resolve(strict=False)))
    if target_norm in os.path.normcase(expanded):
        return True

    try:
        words = shlex.split(expanded, posix=os.name != "nt")
    except ValueError:
        words = expanded.split()
    for word in words:
        candidate = word.strip("'\";,()")
        if candidate.startswith("file:"):
            candidate = candidate[5:].split("?", 1)[0]
        if not candidate.endswith("state.db"):
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = cwd / path
        if os.path.normcase(str(path.resolve(strict=False))) == target_norm:
            return True
    return False


def _source_references_target(source: str, target: Path, cwd: Path) -> bool:
    if _references_target(source, target, cwd):
        return True
    lowered = source.lower()
    return "state.db" in lowered and any(
        marker in lowered
        for marker in ("get_hermes_home", "hermes_home", ".hermes")
    )


def _read_bounded_source(path: Path) -> str | None:
    try:
        if path.is_file() and path.stat().st_size <= 128_000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


def _local_module_path(module: str, cwd: Path) -> Path | None:
    """Resolve only a module physically below cwd; never import it."""
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module):
        return None
    relative = Path(*module.split("."))
    for candidate in (cwd / relative.with_suffix(".py"), cwd / relative / "__main__.py"):
        if candidate.is_file():
            return candidate
    return None


def _referenced_sources(
    command: str, cwd: Path, *, _depth: int = 0
) -> list[tuple[str, str]]:
    """Read bounded local Python/shell sources, including nested carriers."""
    try:
        from tools.approval import (
            _command_detection_variants,
            _deobfuscate_shell_word_for_detection,
            _iter_shell_command_word_spans,
        )
        variants = _command_detection_variants(command)
    except Exception:
        variants = [command]
        _iter_shell_command_word_spans = lambda _command: []
        _deobfuscate_shell_word_for_detection = lambda word: word

    sources: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, source: str) -> None:
        key = (kind, source)
        if key not in seen:
            seen.add(key)
            sources.append(key)

    for variant in variants:
        try:
            words = shlex.split(variant, posix=os.name != "nt")
        except ValueError:
            continue

        for index, word in enumerate(words):
            name = os.path.basename(word).lower()
            args = words[index + 1 :]
            if _PYTHON_NAME_RE.fullmatch(name):
                for arg_index, arg in enumerate(args):
                    if arg == "-c" and arg_index + 1 < len(args):
                        add("python", args[arg_index + 1])
                        break
                    if arg == "-m" and arg_index + 1 < len(args):
                        module_path = _local_module_path(args[arg_index + 1], cwd)
                        if module_path is not None:
                            source = _read_bounded_source(module_path)
                            if source is not None:
                                add("python", source)
                        break
                    if not arg.startswith("-"):
                        path = Path(arg)
                        if not path.is_absolute():
                            path = cwd / path
                        source = _read_bounded_source(path)
                        if source is not None:
                            add("python", source)
                        break
            elif name in _SCRIPT_RUNNER_NAMES:
                for arg in args:
                    if arg.startswith("-"):
                        continue
                    path = Path(arg)
                    if not path.is_absolute():
                        path = cwd / path
                    source = _read_bounded_source(path)
                    if source is not None:
                        add("shell", source)
                    break

        # Directly executed local shebang scripts at shell command positions.
        for _start, _end, word in _iter_shell_command_word_spans(variant):
            decoded = _deobfuscate_shell_word_for_detection(word)
            path = Path(decoded)
            if not path.is_absolute():
                path = cwd / path
            source = _read_bounded_source(path)
            if source is None:
                continue
            first_line = source.splitlines()[0].lower() if source.splitlines() else ""
            if first_line.startswith("#!") and any(
                name in first_line for name in _SCRIPT_RUNNER_NAMES
            ):
                add("shell", source)
            elif first_line.startswith("#!") and (
                "python" in first_line or "pypy" in first_line
            ):
                add("python", source)

    if _depth < 2:
        for kind, source in tuple(sources):
            if kind != "shell":
                continue
            for nested_kind, nested_source in _referenced_sources(
                source, cwd, _depth=_depth + 1
            ):
                add(nested_kind, nested_source)

    return sources


def _python_source_opens_sqlite(source: str) -> bool:
    """Recognize direct sqlite3.connect calls without matching comments/prose."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    module_aliases = {"sqlite3"}
    connect_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            for alias in node.names:
                if alias.name == "connect":
                    connect_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "connect"
            and isinstance(function.value, ast.Name)
            and function.value.id in module_aliases
        ):
            return True
        if isinstance(function, ast.Name) and function.id in connect_aliases:
            return True
    return False


def check_live_state_db_command(
    command: str,
    *,
    env_type: str,
    has_host_access: bool = False,
    target_aliases: tuple[str | os.PathLike[str], ...] = (),
    cwd: str | os.PathLike[str] | None = None,
    hermes_home: str | os.PathLike[str] | None = None,
    gateway_is_live: Callable[[Path], bool] = gateway_is_live,
) -> tuple[bool, str | None]:
    """Return ``(blocked, reason)`` for unsafe ad-hoc live DB access."""
    if env_type != "local" and not has_host_access:
        return False, None

    if hermes_home is None:
        from hermes_constants import get_hermes_home

        profile_home = Path(get_hermes_home()).expanduser()
    else:
        profile_home = Path(hermes_home).expanduser()
    target = (profile_home / "state.db").resolve(strict=False)
    targets = (
        target,
        *(Path(alias).expanduser().resolve(strict=False) for alias in target_aliases),
    )
    command_cwd = Path(cwd or os.getcwd()).expanduser().resolve(strict=False)

    executables = _command_executables(command)
    uses_sqlite = any(_SQLITE_NAME_RE.fullmatch(name) for name in executables)
    uses_python = any(_PYTHON_NAME_RE.fullmatch(name) for name in executables)
    sources = _referenced_sources(command, command_cwd)

    references_target = any(
        _references_target(command, candidate, command_cwd) for candidate in targets
    )
    risky_access = (uses_sqlite or uses_python) and references_target
    if not risky_access:
        for kind, source in sources:
            source_executables = _command_executables(source)
            source_uses_sqlite = kind == "shell" and any(
                _SQLITE_NAME_RE.fullmatch(name) for name in source_executables
            )
            # Python source is interpreted by the outer Python executable; it
            # need not contain a shell-position "python" token itself.
            source_uses_python = (
                kind == "python" and _python_source_opens_sqlite(source)
            )
            source_references_target = any(
                _source_references_target(source, candidate, command_cwd)
                for candidate in targets
            )
            if (source_uses_sqlite or source_uses_python) and source_references_target:
                risky_access = True
                break
    if not risky_access:
        return False, None
    if not gateway_is_live(profile_home):
        return False, None
    return True, _BLOCK_MESSAGE
