"""Strict, single-leaf explanation; never returns arbitrary config or env values."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

KEY = "approvals.destructive_slash_confirm"


def explain_config(key: str, *, profile_selection="environment") -> dict:
    # Reject BEFORE resolving any user-selected path or reading configuration.
    if key != KEY:
        return {"status": "unsupported", "scope": "fresh_cli_resolution",
                "unknown": ["selector_not_allowlisted", "consumer_not_traced"]}
    from hermes_cli import managed_scope
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from hermes_cli.config_layers import resolve_boolean_leaf
    from hermes_constants import get_hermes_home, profile_name_for_home
    from utils import fast_safe_load

    home = get_hermes_home()
    result = {"status": "blocked", "scope": "fresh_cli_resolution", "key": KEY,
              "home": str(home), "profile": profile_name_for_home(home),
              "profile_selection": profile_selection, "unknown": ["running_gateway_value", "existing_session_value"],
              "consumer": "gateway/run_busy.py:GatewayBusySessionMixin._maybe_confirm_destructive_slash",
              "environment": "no_direct_override; reference-valued_layers_unsupported",
              "cli_session": "not_applied; consumer_runtime_state_not_observed",
              "default": {"value": DEFAULT_CONFIG["approvals"]["destructive_slash_confirm"],
                          "definition": "hermes_cli/config_defaults.py:DEFAULT_CONFIG.approvals.destructive_slash_confirm"}}
    from agent.safe_worker_policy import worker_config_snapshot
    if worker_config_snapshot() is not None:
        result["blockers"] = ["worker_snapshot_unsupported"]
        return result
    try:
        user = _read_mapping(home / "config.yaml", fast_safe_load)
        managed_dir = managed_scope.get_managed_dir()
        managed = _read_mapping(managed_dir / "config.yaml", fast_safe_load) if managed_dir else {}
        user_present = _leaf(user)
        managed_present = _leaf(managed)
        # Strict typed validation prevents projecting arbitrary strings, including expanded secrets.
        if any(present and type(value) is not bool for present, value in (user_present, managed_present)):
            result["blockers"] = ["nonboolean_or_environment_reference_unsupported"]
            return result
        value = resolve_boolean_leaf(DEFAULT_CONFIG, user, managed, "approvals", "destructive_slash_confirm")
        if type(value) is not bool:
            result["blockers"] = ["resolved_type_unknown"]
            return result
    except (OSError, ValueError, TypeError, KeyError):
        result["blockers"] = ["configuration_unreadable_or_malformed"]
        return result
    except Exception:
        # YAML exception strings may include unrelated credentials; never serialize them.
        result["blockers"] = ["configuration_resolution_failed"]
        return result
    root = Path(__file__).resolve().parent.parent
    result.update(status="ok", value=value,
                  origin="managed" if managed_present[0] else "profile" if user_present[0] else "default",
                  profile_key_present=user_present[0], managed_key_present=managed_present[0],
                  resolver={"root": str(root),
                      "scope": "partial_source_manifest_current_bytes_not_live_generation",
                      "unhashed_influences": ["Python interpreter", "PyYAML implementation", "filesystem and process environment", "worker policy context"],
                      "limitations": ["literal_boolean_projection_only", "malformed_managed_blocks_instead_of_production_fail_open", "no_backup_recovery_or_runtime_observation"],
                      "files_sha256": {
                      name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                      for name in ("hermes_cli/config_layers.py", "hermes_cli/config.py", "hermes_cli/config_defaults.py",
                                   "hermes_cli/config_explain.py", "hermes_cli/config_explain_entry.py",
                                   "hermes_cli/profile_selection.py", "hermes_cli/profiles.py",
                                   "hermes_cli/managed_scope.py", "hermes_constants.py", "utils.py",
                                   "hermes_yaml.py", "gateway/run_busy.py")}})
    return result


def _read_mapping(path, load):
    try:
        with path.open(encoding="utf-8-sig") as stream:
            value = load(stream)
    except FileNotFoundError:
        return {}
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("mapping required")
    return value


def _leaf(layer):
    section = layer.get("approvals", {})
    if not isinstance(section, dict):
        raise ValueError("mapping required")
    return "destructive_slash_confirm" in section, section.get("destructive_slash_confirm")


def config_explain_command(args):
    result = explain_config(args.key)
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "ok":
        raise SystemExit(2)
