"""Only config explain bypasses application bootstrap. All other argv are untouched."""
import argparse
import json
import os
import sys


def is_explain_command(argv):
    from hermes_cli import _config_explain_argv
    return _config_explain_argv(argv) is not None


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
    from hermes_cli import _config_explain_argv
    args = parser.parse_args(_config_explain_argv(sys.argv[1:]))
    from hermes_cli.config_explain import explain_config
    result = explain_config(args.key, profile_selection=basis)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "ok" else 2)
