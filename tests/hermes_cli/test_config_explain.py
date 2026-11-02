"""Single-leaf provenance stays typed, read-only and scoped to a fresh CLI."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

KEY = "approvals.destructive_slash_confirm"


def invoke(home, managed, key=KEY):
    env = {"PATH": os.environ["PATH"], "HOME": str(home.parent),
           "HERMES_HOME": str(home), "HERMES_MANAGED_DIR": str(managed),
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", "config", "explain", key, "--json"],
                          check=False, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("raw,expected,origin,present", [
    (None, True, "default", False),
    ("approvals:\n  destructive_slash_confirm: false\n", False, "profile", True),
    ("approvals:\n  destructive_slash_confirm: true\n", True, "profile", True),
])
def test_cli_origins(tmp_path, raw, expected, origin, present):
    home = tmp_path / "alternate"; home.mkdir()
    managed = tmp_path / "managed"; managed.mkdir()
    if raw is not None:
        (home / "config.yaml").write_text(raw)
    result = invoke(home, managed)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["value"] is expected
    assert data["origin"] == origin
    assert data["profile_key_present"] is present
    assert data["scope"] == "fresh_cli_resolution"
    assert data["home"] == str(home)
    assert "running_gateway_value" in data["unknown"]
    assert not (home / "backups").exists()


def test_managed_wins(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    managed = tmp_path / "managed"; managed.mkdir()
    (home / "config.yaml").write_text("approvals:\n  destructive_slash_confirm: false\n")
    (managed / "config.yaml").write_text("approvals:\n  destructive_slash_confirm: true\n")
    result = invoke(home, managed)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["value"] is True and data["origin"] == "managed"
    assert data["profile_key_present"] is True


@pytest.mark.parametrize("raw", ["approvals: [", "approvals:\n  destructive_slash_confirm: '${PRIVATE_SENTINEL}'\n", "approvals:\n  destructive_slash_confirm: wrong\n"])
def test_invalid_or_environment_leaf_is_unknown(tmp_path, raw):
    home = tmp_path / "home"; home.mkdir()
    managed = tmp_path / "managed"; managed.mkdir()
    (home / "config.yaml").write_text(raw)
    result = invoke(home, managed)
    assert result.returncode != 0
    data = json.loads(result.stdout)
    assert data["status"] == "blocked" and "value" not in data
    assert "PRIVATE_SENTINEL" not in result.stdout


@pytest.mark.parametrize("key", ["model.api_key", "approvals.enabled", "plugins.dynamic.enabled"])
def test_unsupported_selectors_denied(tmp_path, key):
    home = tmp_path / "home"; home.mkdir()
    managed = tmp_path / "managed"; managed.mkdir()
    result = invoke(home, managed, key)
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "unsupported"
