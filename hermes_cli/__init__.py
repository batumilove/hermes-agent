"""Hermes CLI - Unified command-line interface for Hermes Agent."""

import sys

__release_date__ = "2026.9.24"
# Declared for type checkers and the old-updater surface audit; served lazily by __getattr__.
__version__: str


def __getattr__(name: str) -> str:
    """Old-updater compat: shipped updaters import ``__version__`` after the checkout swap.

    tests/compat/old_updater_surface.json freezes that import. In-tree code resolves
    identity through hermes_cli.version_info.get_version_info(); this reads only the
    install stamp -- never git -- and keeps the pre-stamp placeholder when a checkout
    has no stamp.

    Lazy because ``pm`` is not importable when this package loads: a venv
    editable-installed from a pre-PM tree maps only the top-level packages it knew
    at install time, and the repo root reaches ``sys.path`` only once
    ``hermes_bootstrap`` runs -- after this ``__init__``, from ``hermes_cli.main``.
    """
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from hermes_cli.steward import read_install_stamp
    try:
        from pm.paths import repo_root
    except ModuleNotFoundError as exc:
        if exc.name != "pm" and not (exc.name or "").startswith("pm."):
            raise
        # The old editable finder may not know the new pm package yet.
        import json
        from pathlib import Path
        try:
            stamp = json.loads((Path(__file__).resolve().parents[1] / "install-stamp.json").read_text(encoding="utf-8-sig"))
            return str(stamp.get("baseVersion") or "0.0.0")
        except (OSError, ValueError, AttributeError):
            return "0.0.0"
    return str(read_install_stamp(repo_root()).get("baseVersion") or "0.0.0")


def _config_explain_argv(argv):
    """Identify the real command without importing a possibly torn checkout.

    This bounded snapshot mirrors _parser._add_top_level_flags (parity-tested).
    argparse owns equals, short clusters, abbreviations, optional values and --.
    Unknown/malformed prefixes decline admission; ordinary recovery owns them.
    Only the first positional command/action can admit the read-only route.
    """
    import argparse

    class GateParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)

    parser = GateParser(add_help=False, exit_on_error=False)
    for flags in (
        ('-z', '--oneshot'), ('--usage-file',), ('--cpu-profile',), ('-m', '--model'),
        ('--provider',), ('--reasoning',), ('-t', '--toolsets'),
        ('--resume', '-r'), ('--in',), ('--skills', '-s'),
    ):
        parser.add_argument(*flags)
    parser.add_argument('--continue', '-c', nargs='?')
    for flags in (
        ('--version', '-V'), ('--help', '-h'), ('--no-restore-cwd',),
        ('--worktree', '-w'), ('--accept-hooks',), ('--yolo',),
        ('--pass-session-id',), ('--checkpoints',), ('--ignore-user-config',),
        ('--ignore-rules',), ('--safe-mode',), ('--tui',),
        ('--native', '--tui-native'), ('--cli',), ('--dev',),
    ):
        parser.add_argument(*flags, action='store_true')
    parser.add_argument('command_argv', nargs=argparse.REMAINDER)
    # Profile selection precedes argparse and recognizes literal spellings only.
    # Do not add it to argparse: that makes --pr/--pro ambiguous with --provider.
    import re
    clean = list(argv)
    value_flags = {flag for action in parser._actions if action.nargs != 0
                   for flag in action.option_strings}
    i = 0
    while i < len(clean):
        token = clean[i]
        if token == '--' or (token == '--args' and 'mcp' in clean[:i] and 'add' in clean[:i]):
            break
        if token in {'--profile', '-p'} or token.startswith('--profile='):
            count = 1 if token.startswith('--profile=') else 2
            if count == 2 and i + 1 == len(clean):
                return None
            raw = token.split('=', 1)[1] if count == 1 else clean[i + 1]
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', raw.strip().casefold()):
                return None
            del clean[i:i + count]
            break
        takes_value = '=' not in token and i + 1 < len(clean) and (
            token in value_flags - {'--continue', '-c'}
            or (token in {'--continue', '-c'} and not clean[i + 1].startswith('-'))
        )
        i += 2 if takes_value else 1
    try:
        args, unknown = parser.parse_known_args(clean)
    except (argparse.ArgumentError, ValueError):
        return None
    words = args.command_argv
    if words[:1] == ['--']:
        words = words[1:]
    if unknown or args.help or args.version or words[:2] != ['config', 'explain']:
        return None
    return words


def _ensure_utf8() -> bool:
    """Force UTF-8 stdout/stderr to prevent UnicodeEncodeError crashes; True when a stream was repaired.

    The CLI prints box-drawing characters and the ☤ glyph in the setup wizard, doctor, and status
    banners; under a non-UTF-8 codec that raises before the command can even start (e.g.
    `hermes setup` on a fresh Pi).
    """
    repaired = False
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
                continue
            # Preferred: reconfigure in place, preserving object identity so code already holding
            # a reference to the old sys.stdout benefits from the repair too.
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")
            else:
                # No reconfigure(): reopen the fd as UTF-8 (closefd=False keeps the original fd open).
                new_stream = open(stream.fileno(), "w", encoding="utf-8", errors="replace",  # windows-footgun: ok (stdout re-open for write, not a read)
                                  buffering=1, closefd=False)
                setattr(sys, stream_name, new_stream)
            repaired = True
        except (AttributeError, OSError, ValueError):
            pass
    return repaired


# Import repairs only this process's streams. Gateway, compute host, and test code import this
# package as a library; rewriting their os.environ would leak into every child they spawn, so the
# child-process UTF-8 hint is applied by the CLI entry point (hermes_cli.main.main) instead.
_stdio_repaired = _ensure_utf8()
