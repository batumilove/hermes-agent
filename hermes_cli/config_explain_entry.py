"""Only config explain bypasses application bootstrap. All other argv are untouched."""
import argparse
import json
import os
import sys


def is_explain_command(argv):
    # Reuse parser-owned option arity; option values must never be commands.
    from hermes_cli._parser import top_level_value_flag_sets
    required, optional = top_level_value_flag_sets()
    words = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"-p", "--profile"}:
            i += 2
            continue
        if arg.startswith("--profile="):
            i += 1
            continue
        if arg in required or (arg in optional and i + 1 < len(argv) and not argv[i + 1].startswith("-")):
            i += 2
            continue
        if not arg.startswith("-"):
            words.append(arg)
            if len(words) == 2:
                return words == ["config", "explain"]
        i += 1
    return False


def maybe_dispatch():
    if not is_explain_command(sys.argv[1:]):
        return
    from hermes_cli import profile_selection as selection
    from hermes_cli import _startup_fast
    _startup_fast.normalize_hermes_home_env()
    explicit = selection._scan_profile_flag(sys.argv[1:])[0] is not None
    old_home = os.environ.get("HERMES_HOME")
    selection._apply_profile_override()
    basis = "explicit" if explicit else "sticky" if os.environ.get("HERMES_HOME") != old_home else "environment" if old_home else "default"
    parser = argparse.ArgumentParser(prog="hermes config explain", allow_abbrev=False)
    parser.add_argument("command", choices=["config"])
    parser.add_argument("action", choices=["explain"])
    parser.add_argument("key")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    from hermes_cli.config_explain import explain_config
    result = explain_config(args.key, profile_selection=basis)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "ok" else 2)
