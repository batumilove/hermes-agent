"""Tests for plugins/memory/honcho/client.py — Honcho client configuration."""

import importlib.util
import json
import os
import socket
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

from hermes_cli.profiles import _get_default_hermes_home

import pytest

from plugins.memory.honcho.client import (
    HonchoClientConfig,
    get_honcho_client,
    profile_host_key,
    reset_honcho_client,
    resolve_active_host,
    resolve_config_path,
    resolve_global_config_path,
)


class TestHonchoClientConfigDefaults:
    def test_default_values(self):
        config = HonchoClientConfig()
        assert config.host == "hermes"
        assert config.workspace_id == "hermes"
        assert config.api_key is None
        assert config.environment == "production"
        assert config.timeout is None
        assert config.enabled is False
        assert config.save_messages is True
        assert config.session_strategy == "per-directory"
        assert config.recall_mode == "hybrid"
        assert config.session_peer_prefix is False
        assert config.sessions == {}


class TestFromEnv:
    def test_reads_api_key_from_env(self):
        with patch.dict(os.environ, {"HONCHO_API_KEY": "test-key-123"}):
            config = HonchoClientConfig.from_env()
        assert config.api_key == "test-key-123"
        assert config.enabled is True

    def test_reads_environment_from_env(self):
        with patch.dict(os.environ, {
            "HONCHO_API_KEY": "key",
            "HONCHO_ENVIRONMENT": "staging",
        }):
            config = HonchoClientConfig.from_env()
        assert config.environment == "staging"

    def test_defaults_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove HONCHO_API_KEY if it exists
            os.environ.pop("HONCHO_API_KEY", None)
            os.environ.pop("HONCHO_ENVIRONMENT", None)
            config = HonchoClientConfig.from_env()
        assert config.api_key is None
        assert config.environment == "production"

    def test_custom_workspace(self):
        config = HonchoClientConfig.from_env(workspace_id="custom")
        assert config.workspace_id == "custom"

    def test_reads_base_url_from_env(self):
        with patch.dict(os.environ, {"HONCHO_BASE_URL": "http://localhost:8000"}, clear=False):
            config = HonchoClientConfig.from_env()
        assert config.base_url == "http://localhost:8000"
        assert config.enabled is True

    def test_enabled_without_api_key_when_base_url_set(self):
        """base_url alone (no API key) is sufficient to enable a local instance."""
        with patch.dict(os.environ, {"HONCHO_BASE_URL": "http://localhost:8000"}, clear=False):
            os.environ.pop("HONCHO_API_KEY", None)
            config = HonchoClientConfig.from_env()
        assert config.api_key is None
        assert config.base_url == "http://localhost:8000"
        assert config.enabled is True

    def test_reads_timeout_from_env(self):
        with patch.dict(os.environ, {"HONCHO_TIMEOUT": "90"}, clear=True):
            config = HonchoClientConfig.from_env()
        assert config.timeout == 90.0


class TestFromGlobalConfig:
    def test_missing_config_falls_back_to_env(self, tmp_path):
        with patch.dict(os.environ, {}, clear=True):
            config = HonchoClientConfig.from_global_config(
                config_path=tmp_path / "nonexistent.json"
            )
        # Should fall back to from_env
        assert config.enabled is False
        assert config.api_key is None

    def test_reads_full_config(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "***",
            "workspace": "my-workspace",
            "environment": "staging",
            "peerName": "alice",
            "aiPeer": "hermes-custom",
            "enabled": True,
            "saveMessages": False,
            "contextTokens": 2000,
            "sessionStrategy": "per-project",
            "sessionPeerPrefix": True,
            "sessions": {"/home/user/proj": "my-session"},
            "hosts": {
                "hermes": {
                    "workspace": "override-ws",
                    "aiPeer": "override-ai",
                }
            }
        }))
        # Isolate from real ~/.hermes/honcho.json
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "isolated"))

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.api_key == "***"
        # Host block workspace overrides root workspace
        assert config.workspace_id == "override-ws"
        assert config.ai_peer == "override-ai"
        assert config.environment == "staging"
        assert config.peer_name == "alice"
        assert config.enabled is True
        assert config.save_messages is False
        assert config.session_strategy == "per-project"
        assert config.session_peer_prefix is True

    def test_host_block_overrides_root(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "key",
            "workspace": "root-ws",
            "aiPeer": "root-ai",
            "hosts": {
                "hermes": {
                    "workspace": "host-ws",
                    "aiPeer": "host-ai",
                }
            }
        }))

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.workspace_id == "host-ws"
        assert config.ai_peer == "host-ai"

    def test_root_fields_used_when_no_host_block(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "key",
            "workspace": "root-ws",
            "aiPeer": "root-ai",
        }))

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.workspace_id == "root-ws"
        assert config.ai_peer == "root-ai"

    def test_session_strategy_default_from_global_config(self, tmp_path):
        """from_global_config with no sessionStrategy should match dataclass default."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***"}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.session_strategy == "per-directory"

    def test_context_tokens_default_is_none(self, tmp_path):
        """Default context_tokens should be None (uncapped) unless explicitly set."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***"}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.context_tokens is None

    def test_context_tokens_explicit_sets_cap(self, tmp_path):
        """Explicit contextTokens in config sets the cap."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***", "contextTokens": 1200}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.context_tokens == 1200

    def test_context_tokens_explicit_overrides_default(self, tmp_path):
        """Explicit contextTokens in config should override the default."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***", "contextTokens": 2000}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.context_tokens == 2000

    def test_context_tokens_host_block_wins(self, tmp_path):
        """Host block contextTokens should override root."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "key",
            "contextTokens": 1000,
            "hosts": {"hermes": {"contextTokens": 2000}},
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.context_tokens == 2000

    def test_recall_mode_from_config(self, tmp_path):
        """recallMode is read from config, host block wins."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "key",
            "recallMode": "tools",
            "hosts": {"hermes": {"recallMode": "context"}},
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.recall_mode == "context"

    def test_recall_mode_default(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "key"}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.recall_mode == "hybrid"

    def test_corrupt_config_falls_back_to_env(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json{{{")

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        # Should fall back to from_env without crashing
        assert isinstance(config, HonchoClientConfig)

    def test_api_key_env_fallback(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"enabled": True}))

        with patch.dict(os.environ, {"HONCHO_API_KEY": "env-key"}):
            config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.api_key == "env-key"

    def test_base_url_env_fallback(self, tmp_path):
        """HONCHO_BASE_URL env var is used when no baseUrl in config JSON."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"workspace": "local"}))

        with patch.dict(os.environ, {"HONCHO_BASE_URL": "http://localhost:8000"}, clear=False):
            config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.base_url == "http://localhost:8000"
        assert config.enabled is True

    def test_base_url_from_config_root(self, tmp_path):
        """baseUrl in config root is read and takes precedence over env var."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"baseUrl": "http://config-host:9000"}))

        with patch.dict(os.environ, {"HONCHO_BASE_URL": "http://localhost:8000"}, clear=False):
            config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.base_url == "http://config-host:9000"

    def test_base_url_not_read_from_host_block(self, tmp_path):
        """baseUrl is a root-level connection setting, not overridable per-host (consistent with apiKey)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "baseUrl": "http://root:9000",
            "hosts": {"hermes": {"baseUrl": "http://host-block:9001"}},
        }))

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.base_url == "http://root:9000"

    def test_timeout_from_config_root(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"timeout": 75}))

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.timeout == 75.0

    def test_request_timeout_alias_from_config_root(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"requestTimeout": "82.5"}))

        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.timeout == 82.5


class TestResolveSessionName:
    def test_manual_override(self):
        config = HonchoClientConfig(sessions={"/home/user/proj": "custom-session"})
        assert config.resolve_session_name("/home/user/proj") == "custom-session"

    def test_derive_from_dirname(self):
        config = HonchoClientConfig()
        result = config.resolve_session_name("/home/user/my-project")
        assert result == "my-project"

    def test_peer_prefix(self):
        config = HonchoClientConfig(peer_name="alice", session_peer_prefix=True)
        result = config.resolve_session_name("/home/user/proj")
        assert result == "alice-proj"

    def test_no_peer_prefix_when_no_peer_name(self):
        config = HonchoClientConfig(session_peer_prefix=True)
        result = config.resolve_session_name("/home/user/proj")
        assert result == "proj"

    def test_default_cwd(self):
        config = HonchoClientConfig()
        result = config.resolve_session_name()
        # Should use os.getcwd() basename
        assert result == Path.cwd().name

    def test_per_repo_uses_git_root(self):
        config = HonchoClientConfig(session_strategy="per-repo")
        with patch.object(
            HonchoClientConfig, "_git_repo_name", return_value="hermes-agent"
        ):
            result = config.resolve_session_name("/home/user/hermes-agent/subdir")
        assert result == "hermes-agent"

    def test_per_repo_with_peer_prefix(self):
        config = HonchoClientConfig(
            session_strategy="per-repo", peer_name="eri", session_peer_prefix=True
        )
        with patch.object(
            HonchoClientConfig, "_git_repo_name", return_value="groudon"
        ):
            result = config.resolve_session_name("/home/user/groudon/src")
        assert result == "eri-groudon"

    def test_per_repo_falls_back_to_dirname_outside_git(self):
        config = HonchoClientConfig(session_strategy="per-repo")
        with patch.object(
            HonchoClientConfig, "_git_repo_name", return_value=None
        ):
            result = config.resolve_session_name("/home/user/not-a-repo")
        assert result == "not-a-repo"

    def test_per_repo_manual_override_still_wins(self):
        config = HonchoClientConfig(
            session_strategy="per-repo",
            sessions={"/home/user/proj": "custom-session"},
        )
        result = config.resolve_session_name("/home/user/proj")
        assert result == "custom-session"


class TestResolveConfigPath:
    def test_prefers_hermes_home_when_exists(self, tmp_path):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        local_cfg = hermes_home / "honcho.json"
        local_cfg.write_text('{"apiKey": "local"}')

        with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}):
            result = resolve_config_path()
        assert result == local_cfg

    def test_falls_back_to_default_profile_when_no_local(self, tmp_path, monkeypatch):
        # Profile mode: HERMES_HOME points at ~/.hermes/profiles/<name>, so
        # _get_default_hermes_home() must resolve back to ~/.hermes — that's
        # the bug the HOME-anchored helper fixes (vs. blindly using Path.home()).
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        default_home = fake_home / ".hermes"
        profile_home = default_home / "profiles" / "work"
        profile_home.mkdir(parents=True)
        default_cfg = default_home / "honcho.json"
        default_cfg.write_text('{"apiKey": "default-key"}')

        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setenv("HERMES_HOME", str(profile_home))

        result = resolve_config_path()

        assert _get_default_hermes_home() == default_home
        assert result == default_cfg

    def test_falls_back_to_global_without_hermes_home_env(self, tmp_path):
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        with patch.dict(os.environ, {}, clear=False), \
             patch.object(Path, "home", return_value=fake_home):
            os.environ.pop("HERMES_HOME", None)
            result = resolve_config_path()
        assert result == fake_home / ".honcho" / "config.json"

    def test_global_fallback_uses_home_at_call_time(self, tmp_path):
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}), \
             patch.object(Path, "home", return_value=fake_home):
            assert resolve_global_config_path() == fake_home / ".honcho" / "config.json"
            assert resolve_config_path() == fake_home / ".honcho" / "config.json"

    def test_from_global_config_uses_default_profile_fallback(self, tmp_path, monkeypatch):
        # Profile mode: from_global_config() reads the default-profile honcho.json
        # via the HOME-anchored helper, not Path.home() / ".hermes".
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        default_home = fake_home / ".hermes"
        profile_home = default_home / "profiles" / "work"
        profile_home.mkdir(parents=True)
        default_cfg = default_home / "honcho.json"
        default_cfg.write_text(json.dumps({
            "apiKey": "default-key",
            "workspace": "default-ws",
        }))

        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setenv("HERMES_HOME", str(profile_home))

        config = HonchoClientConfig.from_global_config()

        assert config.api_key == "default-key"
        assert config.workspace_id == "default-ws"

    def test_from_global_config_uses_local_path(self, tmp_path):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        local_cfg = hermes_home / "honcho.json"
        local_cfg.write_text(json.dumps({
            "apiKey": "***",
            "workspace": "local-ws",
        }))

        with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}), \
             patch.object(Path, "home", return_value=tmp_path):
            config = HonchoClientConfig.from_global_config()
        assert config.api_key == "***"
        assert config.workspace_id == "local-ws"


class TestResolveActiveHost:
    def test_profile_host_key_uses_honcho_safe_separator(self):
        assert profile_host_key("coder") == "hermes_coder"
        assert profile_host_key("default") == "hermes"

    def test_default_returns_hermes(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            os.environ.pop("HERMES_HOME", None)
            with patch(
                "plugins.memory.honcho.client.resolve_config_path",
                return_value=Path("/nonexistent/honcho.json"),
            ):
                assert resolve_active_host() == "hermes"

    def test_explicit_env_var_wins(self):
        with patch.dict(os.environ, {"HERMES_HONCHO_HOST": "hermes.coder"}):
            assert resolve_active_host() == "hermes.coder"

    def test_profile_name_derives_host(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            with patch("hermes_cli.profiles.get_active_profile_name", return_value="coder"):
                assert resolve_active_host() == "hermes_coder"

    def test_default_host_does_not_override_named_profile(self, tmp_path):
        """defaultHost is not applied before active-profile resolution."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "defaultHost": "local",
            "hosts": {"local": {"workspace": "local-ws"}},
        }))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            with patch("hermes_cli.profiles.get_active_profile_name", return_value="coder"), \
                 patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
                assert resolve_active_host() == "hermes_coder"

    def test_default_host_applies_to_default_profile_only(self, tmp_path):
        """default profile can use setup-generated defaultHost without leaking to other profiles."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "defaultHost": "local",
            "hosts": {"local": {"workspace": "local-ws"}},
        }))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
                 patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
                assert resolve_active_host() == "local"

    def test_default_profile_returns_hermes(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
                 patch(
                     "plugins.memory.honcho.client.resolve_config_path",
                     return_value=Path("/nonexistent/honcho.json"),
                 ):
                assert resolve_active_host() == "hermes"

    def test_custom_profile_returns_hermes(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            with patch("hermes_cli.profiles.get_active_profile_name", return_value="custom"), \
                 patch(
                     "plugins.memory.honcho.client.resolve_config_path",
                     return_value=Path("/nonexistent/honcho.json"),
                 ):
                assert resolve_active_host() == "hermes"

    def test_profiles_import_failure_falls_back(self):
        import sys
        with patch.dict(os.environ, {}, clear=False), patch(
            "plugins.memory.honcho.client.resolve_config_path",
            return_value=Path("/nonexistent/test-honcho-config.json"),
        ):
            os.environ.pop("HERMES_HONCHO_HOST", None)
            # Temporarily remove hermes_cli.profiles to simulate import failure
            saved = sys.modules.get("hermes_cli.profiles")
            sys.modules["hermes_cli.profiles"] = None  # type: ignore
            try:
                assert resolve_active_host() == "hermes"
            finally:
                if saved is not None:
                    sys.modules["hermes_cli.profiles"] = saved
                else:
                    sys.modules.pop("hermes_cli.profiles", None)


class TestProfileScopedConfig:
    def test_from_env_uses_profile_host(self):
        with patch.dict(os.environ, {"HONCHO_API_KEY": "key"}):
            config = HonchoClientConfig.from_env(host="hermes_coder")
        assert config.host == "hermes_coder"
        assert config.workspace_id == "hermes"  # shared workspace
        assert config.ai_peer == "hermes_coder"

    def test_from_env_default_workspace_preserved_for_default_host(self):
        with patch.dict(os.environ, {"HONCHO_API_KEY": "key"}):
            config = HonchoClientConfig.from_env(host="hermes")
        assert config.host == "hermes"
        assert config.workspace_id == "hermes"

    def test_from_global_config_reads_profile_host_block(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "shared-key",
            "hosts": {
                "hermes": {"aiPeer": "hermes", "peerName": "alice"},
                "hermes_coder": {
                    "aiPeer": "hermes_coder",
                    "peerName": "alice-coder",
                    "workspace": "coder-ws",
                },
            },
        }))
        config = HonchoClientConfig.from_global_config(
            host="hermes_coder", config_path=config_file,
        )
        assert config.host == "hermes_coder"
        assert config.workspace_id == "coder-ws"
        assert config.ai_peer == "hermes_coder"
        assert config.peer_name == "alice-coder"

    def test_from_global_config_auto_resolves_host(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "key",
            "hosts": {
                "hermes_dreamer": {"peerName": "dreamer-user"},
            },
        }))
        with patch("plugins.memory.honcho.client.resolve_active_host", return_value="hermes_dreamer"):
            config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.host == "hermes_dreamer"
        assert config.peer_name == "dreamer-user"

    def test_from_global_config_reads_legacy_dot_profile_host_block(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "key",
            "hosts": {
                "hermes.dreamer": {"peerName": "dreamer-user"},
            },
        }))
        config = HonchoClientConfig.from_global_config(
            host="hermes_dreamer",
            config_path=config_file,
        )
        assert config.host == "hermes_dreamer"
        assert config.peer_name == "dreamer-user"
        assert config.workspace_id == "hermes_dreamer"


class TestObservationModeMigration:
    """Existing configs without explicit observationMode keep 'unified' default."""

    def test_existing_config_defaults_to_unified(self, tmp_path):
        """Config with host block but no observationMode → 'unified' (old default)."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "apiKey": "k",
            "hosts": {"hermes": {"enabled": True, "aiPeer": "hermes"}},
        }))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.observation_mode == "unified"

    def test_new_config_defaults_to_directional(self, tmp_path):
        """Config with no host block and no credentials → 'directional' (new default)."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({}))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.observation_mode == "directional"

    def test_explicit_directional_respected(self, tmp_path):
        """Existing config with explicit observationMode → uses what's set."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "apiKey": "k",
            "hosts": {"hermes": {"enabled": True, "observationMode": "directional"}},
        }))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.observation_mode == "directional"

    def test_explicit_unified_respected(self, tmp_path):
        """Existing config with explicit observationMode unified → stays unified."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "apiKey": "k",
            "observationMode": "unified",
            "hosts": {"hermes": {"enabled": True}},
        }))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        assert cfg.observation_mode == "unified"

    def test_granular_observation_overrides_preset(self, tmp_path):
        """Explicit observation object overrides both preset and migration default."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "apiKey": "k",
            "hosts": {"hermes": {
                "enabled": True,
                "observation": {
                    "user": {"observeMe": True, "observeOthers": False},
                    "ai": {"observeMe": False, "observeOthers": True},
                },
            }},
        }))
        cfg = HonchoClientConfig.from_global_config(config_path=cfg_file)
        # observation_mode falls back to "unified" (migration), but
        # granular booleans from the observation object win
        assert cfg.user_observe_me is True
        assert cfg.user_observe_others is False
        assert cfg.ai_observe_me is False
        assert cfg.ai_observe_others is True


class TestGetHonchoClient:
    def teardown_method(self):
        reset_honcho_client()

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_passes_timeout_from_config(self):
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="test-key",
            timeout=91.0,
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho:
            client = get_honcho_client(cfg)

        assert client is fake_honcho
        mock_honcho.assert_called_once()
        assert mock_honcho.call_args.kwargs["timeout"] == 91.0

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_hermes_config_timeout_override_used_when_config_timeout_missing(self):
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="test-key",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={"honcho": {"timeout": 88}}):
            client = get_honcho_client(cfg)

        assert client is fake_honcho
        mock_honcho.assert_called_once()
        assert mock_honcho.call_args.kwargs["timeout"] == 88.0

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_defaults_to_30s_when_no_timeout_configured(self):
        from plugins.memory.honcho.client import _DEFAULT_HTTP_TIMEOUT

        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="test-key",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            client = get_honcho_client(cfg)

        assert client is fake_honcho
        mock_honcho.assert_called_once()
        assert mock_honcho.call_args.kwargs["timeout"] == _DEFAULT_HTTP_TIMEOUT

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_hermes_request_timeout_alias_used(self):
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="test-key",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={"honcho": {"request_timeout": "77.5"}}):
            client = get_honcho_client(cfg)

        assert client is fake_honcho
        mock_honcho.assert_called_once()
        assert mock_honcho.call_args.kwargs["timeout"] == 77.5

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_timeout_change_triggers_client_rebuild(self):
        """Changing timeout config must rebuild the cached client."""
        from hermes_constants import get_hermes_home

        cfg_yaml = get_hermes_home() / "config.yaml"
        cfg_yaml.write_text("honcho:\n  timeout: 30\n")

        fake_honcho_1 = MagicMock(name="Honcho_v1")
        fake_honcho_2 = MagicMock(name="Honcho_v2")
        cfg = HonchoClientConfig(
            api_key="test-key",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho_1) as mock_h1:
            client1 = get_honcho_client(cfg)

        assert client1 is fake_honcho_1
        assert mock_h1.call_args.kwargs["timeout"] == 30.0

        # Same config — should return cached client (no rebuild)
        with patch("honcho.Honcho", return_value=fake_honcho_2) as mock_h2:
            client2 = get_honcho_client(cfg)

        assert client2 is fake_honcho_1  # still cached
        mock_h2.assert_not_called()

        # Changed timeout — must rebuild
        cfg_yaml.write_text("honcho:\n  timeout: 300\n")
        st = cfg_yaml.stat()
        os.utime(cfg_yaml, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

        with patch("honcho.Honcho", return_value=fake_honcho_2) as mock_h3:
            client3 = get_honcho_client(cfg)

        assert client3 is fake_honcho_2  # rebuilt
        mock_h3.assert_called_once()
        assert mock_h3.call_args.kwargs["timeout"] == 300.0

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_managed_config_timeout_does_not_thrash_singleton(self, tmp_path, monkeypatch):
        """A managed-scope honcho.timeout with no user config.yaml must be seen
        by the staleness check (stable reuse), and a managed edit must trigger
        a rebuild. Regression for a memo that keyed only on the user file."""
        managed_dir = tmp_path / "managed"
        managed_dir.mkdir()
        managed_cfg = managed_dir / "config.yaml"
        managed_cfg.write_text("honcho:\n  timeout: 88\n")
        monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed_dir))

        fake_honcho_1 = MagicMock(name="Honcho_v1")
        fake_honcho_2 = MagicMock(name="Honcho_v2")
        cfg = HonchoClientConfig(
            api_key="test-key",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho_1) as mock_h1:
            client1 = get_honcho_client(cfg)
            client2 = get_honcho_client(cfg)

        assert client1 is fake_honcho_1
        assert client2 is fake_honcho_1
        assert mock_h1.call_count == 1
        assert mock_h1.call_args.kwargs["timeout"] == 88.0

        # A managed-timeout edit is detected (same-size write, so bump mtime).
        managed_cfg.write_text("honcho:\n  timeout: 99\n")
        st = managed_cfg.stat()
        os.utime(managed_cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

        with patch("honcho.Honcho", return_value=fake_honcho_2) as mock_h2:
            client3 = get_honcho_client(cfg)

        assert client3 is fake_honcho_2
        mock_h2.assert_called_once()
        assert mock_h2.call_args.kwargs["timeout"] == 99.0

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_honcho_json_timeout_does_not_thrash_singleton(self, tmp_path):
        """Regression: a timeout configured in honcho.json must not rebuild the
        client on every no-config call. The staleness check used to resolve the
        timeout without reading honcho.json, so it permanently disagreed with
        the built client and reset the singleton on each access."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "apiKey": "cloud-key",
            "hosts": {"hermes": {"workspace": "ws", "requestTimeout": 120}},
        }))

        fake_honcho_1 = MagicMock(name="Honcho_v1")
        fake_honcho_2 = MagicMock(name="Honcho_v2")

        with patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file), \
             patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
            with patch("honcho.Honcho", return_value=fake_honcho_1) as mock_h1:
                client1 = get_honcho_client()

            assert client1 is fake_honcho_1
            assert mock_h1.call_args.kwargs["timeout"] == 120.0

            # Repeated no-config calls (the session manager hot path) must
            # return the cached client, not rebuild.
            with patch("honcho.Honcho", return_value=fake_honcho_2) as mock_h2:
                client2 = get_honcho_client()
                client3 = get_honcho_client()

            assert client2 is fake_honcho_1
            assert client3 is fake_honcho_1
            mock_h2.assert_not_called()

            # A real honcho.json timeout change is still detected.
            config_file.write_text(json.dumps({
                "apiKey": "cloud-key",
                "hosts": {"hermes": {"workspace": "ws", "requestTimeout": 240}},
            }))
            st = config_file.stat()
            os.utime(config_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

            with patch("honcho.Honcho", return_value=fake_honcho_2) as mock_h3:
                client4 = get_honcho_client()

            assert client4 is fake_honcho_2
            mock_h3.assert_called_once()
            assert mock_h3.call_args.kwargs["timeout"] == 240.0


class TestResolveSessionNameGatewayKey:
    """Regression tests for gateway_session_key priority in resolve_session_name.

    Ensures gateway platforms get stable per-chat Honcho sessions even when
    sessionStrategy=per-session would otherwise create ephemeral sessions.
    Regression: plugin refactor 924bc67e dropped gateway key plumbing.
    """

    def test_gateway_key_overrides_per_session_strategy(self):
        """gateway_session_key must win over per-session session_id."""
        config = HonchoClientConfig(session_strategy="per-session")
        result = config.resolve_session_name(
            session_id="20260412_171002_69bb38",
            gateway_session_key="agent:main:telegram:dm:8439114563",
        )
        assert result == "agent-main-telegram-dm-8439114563"

    def test_gateway_key_not_remapped_by_title(self):
        """A title never remaps a stable identifier — the gateway per-chat key
        wins over the title so a generated title can't split a live conversation
        onto a new Honcho session."""
        config = HonchoClientConfig(session_strategy="per-session")
        result = config.resolve_session_name(
            session_title="my-custom-title",
            session_id="20260412_171002_69bb38",
            gateway_session_key="agent:main:telegram:dm:8439114563",
        )
        assert result == "agent-main-telegram-dm-8439114563"

    def test_per_session_fallback_without_gateway_key(self):
        """Without gateway_session_key, per-session returns session_id (CLI path)."""
        config = HonchoClientConfig(session_strategy="per-session")
        result = config.resolve_session_name(
            session_id="20260412_171002_69bb38",
            gateway_session_key=None,
        )
        assert result == "20260412_171002_69bb38"

    def test_gateway_key_sanitizes_special_chars(self):
        """Colons and other non-alphanumeric chars are replaced with hyphens."""
        config = HonchoClientConfig()
        result = config.resolve_session_name(
            gateway_session_key="agent:main:telegram:dm:8439114563",
        )
        assert result == "agent-main-telegram-dm-8439114563"
        assert ":" not in result


class TestResolveSessionNameLengthLimit:
    """Regression tests for Honcho's 100-char session ID limit (issue #13868).

    Long gateway session keys (Matrix room+event IDs, Telegram supergroup
    reply chains, Slack thread IDs with long workspace prefixes) can overflow
    Honcho's 100-char session_id limit after sanitization. Before this fix,
    every Honcho API call for those sessions 400'd with "session_id too long".
    """

    HONCHO_MAX = 100

    def test_short_gateway_key_unchanged(self):
        """Short keys must not get a hash suffix appended."""
        config = HonchoClientConfig()
        result = config.resolve_session_name(
            gateway_session_key="agent:main:telegram:dm:8439114563",
        )
        # Unchanged fast-path: sanitize only, no truncation, no hash suffix.
        assert result == "agent-main-telegram-dm-8439114563"
        assert len(result) <= self.HONCHO_MAX

    def test_key_at_exact_limit_unchanged(self):
        """A sanitized key that is exactly 100 chars must be returned as-is."""
        key = "a" * self.HONCHO_MAX
        config = HonchoClientConfig()
        result = config.resolve_session_name(gateway_session_key=key)
        assert result == key
        assert len(result) == self.HONCHO_MAX

    def test_long_gateway_key_truncated_to_limit(self):
        """An over-limit sanitized key must truncate to exactly 100 chars."""
        key = "!roomid:matrix.example.org|" + "$event_" + ("a" * 300)
        config = HonchoClientConfig()
        result = config.resolve_session_name(gateway_session_key=key)
        assert result is not None
        assert len(result) == self.HONCHO_MAX

    def test_truncation_is_deterministic(self):
        """Same long key must always produce the same truncated session ID."""
        key = "matrix-" + ("a" * 300)
        config = HonchoClientConfig()
        first = config.resolve_session_name(gateway_session_key=key)
        second = config.resolve_session_name(gateway_session_key=key)
        assert first == second

    def test_truncated_result_respects_char_allowlist(self):
        """Truncated result must still match Honcho's [a-zA-Z0-9_-] allowlist."""
        import re
        key = "slack:T12345:thread-reply:" + ("x" * 300) + ":with:colons:and:slashes/here"
        config = HonchoClientConfig()
        result = config.resolve_session_name(gateway_session_key=key)
        assert result is not None
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", result)

    def test_distinct_long_keys_do_not_collide(self):
        """Two long keys sharing a prefix must produce different truncated IDs."""
        prefix = "matrix:!room:example.org|" + "a" * 200
        key_a = prefix + "-suffix-alpha"
        key_b = prefix + "-suffix-beta"
        config = HonchoClientConfig()
        result_a = config.resolve_session_name(gateway_session_key=key_a)
        result_b = config.resolve_session_name(gateway_session_key=key_b)
        assert result_a != result_b
        assert len(result_a) == self.HONCHO_MAX
        assert len(result_b) == self.HONCHO_MAX

    def test_truncated_result_has_hash_suffix(self):
        """Truncated IDs must end with '-<8 hex chars>' for collision resistance."""
        import re
        key = "matrix-" + ("a" * 300)
        config = HonchoClientConfig()
        result = config.resolve_session_name(gateway_session_key=key)
        # Last 9 chars: '-' + 8 hex chars.
        assert re.search(r"-[0-9a-f]{8}$", result)


class TestResetHonchoClient:
    def test_reset_clears_singleton(self):
        import plugins.memory.honcho.client as mod

        # Seed the cached client through the slot's public surface, then
        # verify reset_honcho_client() clears it. (The client is cached in
        # mod._honcho_client_slot, a thread-safe SingletonSlot, not a bare
        # module global anymore — see #24759.)
        mod._honcho_client_slot.get(lambda: MagicMock())
        assert mod._honcho_client_slot.peek() is not None
        reset_honcho_client()
        assert mod._honcho_client_slot.peek() is None


class TestDialecticDepthParsing:
    """Tests for _parse_dialectic_depth and _parse_dialectic_depth_levels."""

    def test_default_depth_is_1(self, tmp_path):
        """Default dialecticDepth should be 1."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***"}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth == 1

    def test_depth_from_root(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***", "dialecticDepth": 2}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth == 2

    def test_depth_host_block_wins(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "***",
            "dialecticDepth": 1,
            "hosts": {"hermes": {"dialecticDepth": 3}},
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth == 3

    def test_depth_clamped_high(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***", "dialecticDepth": 10}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth == 3

    def test_depth_clamped_low(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***", "dialecticDepth": -1}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth == 1

    def test_depth_levels_default_none(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"apiKey": "***"}))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth_levels is None

    def test_depth_levels_from_config(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "***",
            "dialecticDepth": 2,
            "dialecticDepthLevels": ["minimal", "high"],
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth_levels == ["minimal", "high"]

    def test_depth_levels_padded_if_short(self, tmp_path):
        """Array shorter than depth gets padded with 'low'."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "***",
            "dialecticDepth": 3,
            "dialecticDepthLevels": ["high"],
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth_levels == ["high", "low", "low"]

    def test_depth_levels_truncated_if_long(self, tmp_path):
        """Array longer than depth gets truncated."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "***",
            "dialecticDepth": 1,
            "dialecticDepthLevels": ["high", "max", "medium"],
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth_levels == ["high"]

    def test_depth_levels_invalid_values_default_to_low(self, tmp_path):
        """Invalid reasoning levels in the array fall back to 'low'."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "apiKey": "***",
            "dialecticDepth": 2,
            "dialecticDepthLevels": ["invalid", "high"],
        }))
        config = HonchoClientConfig.from_global_config(config_path=config_file)
        assert config.dialectic_depth_levels == ["low", "high"]


class TestGetHonchoClientBaseUrlDoublePrefixFix:
    """Regression tests for #20688 — Honcho SDK double-prefixing of /v3 for
    self-hosted instances where base_url already contains a version path."""

    def teardown_method(self):
        reset_honcho_client()

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_local_base_url_with_v3_suffix_stripped(self):
        """base_url 'http://localhost:38000/v3' must become 'http://localhost:38000'
        before passing to the Honcho SDK to avoid double '/v3/v3' prefixing."""
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key=None,
            base_url="http://localhost:38000/v3",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        passed_base_url = mock_honcho.call_args.kwargs.get("base_url")
        assert passed_base_url == "http://localhost:38000", (
            f"Expected 'http://localhost:38000', got {passed_base_url!r}"
        )

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_local_base_url_without_version_unchanged(self):
        """base_url 'http://localhost:38000' (no version) must be passed unchanged."""
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key=None,
            base_url="http://localhost:38000",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        passed_base_url = mock_honcho.call_args.kwargs.get("base_url")
        assert passed_base_url == "http://localhost:38000", (
            f"Expected 'http://localhost:38000', got {passed_base_url!r}"
        )

    def test_lan_default_host_empty_key_uses_local_placeholder(self, tmp_path):
        """Regression for #61661: setup-style root baseUrl + defaultHost + LAN IP
        must not pass an empty/None api_key to the SDK for a no-auth local server."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "defaultHost": "local",
            "baseUrl": "http://192.168.2.112:8000",
            "hosts": {
                "local": {
                    "workspace": "local-ws",
                    "aiPeer": "local-ai",
                    "apiKey": "",
                },
            },
        }))

        with patch.dict(os.environ, {}, clear=True), \
             patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            cfg = HonchoClientConfig.from_global_config(config_path=config_file)

        assert cfg.host == "local"
        assert cfg.workspace_id == "local-ws"
        assert cfg.ai_peer == "local-ai"
        assert cfg.api_key is None
        assert cfg.base_url == "http://192.168.2.112:8000"

        fake_honcho = MagicMock(name="Honcho")
        mock_honcho = MagicMock(return_value=fake_honcho)
        fake_honcho_module = types.SimpleNamespace(Honcho=mock_honcho)
        with patch.dict(sys.modules, {"honcho": fake_honcho_module}), \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        assert mock_honcho.call_args.kwargs["api_key"] == "local"
        assert mock_honcho.call_args.kwargs["base_url"] == "http://192.168.2.112:8000"

    def test_lan_default_host_explicit_host_key_preserved(self, tmp_path):
        """A host-block local JWT still wins for LAN/VPN local URLs."""
        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "defaultHost": "local",
            "baseUrl": "http://192.168.2.112:8000",
            "hosts": {
                "local": {
                    "workspace": "local-ws",
                    "aiPeer": "local-ai",
                    "apiKey": "local-jwt",
                },
            },
        }))

        with patch.dict(os.environ, {}, clear=True), \
             patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("plugins.memory.honcho.client.resolve_config_path", return_value=config_file):
            cfg = HonchoClientConfig.from_global_config(config_path=config_file)

        fake_honcho = MagicMock(name="Honcho")
        mock_honcho = MagicMock(return_value=fake_honcho)
        fake_honcho_module = types.SimpleNamespace(Honcho=mock_honcho)
        with patch.dict(sys.modules, {"honcho": fake_honcho_module}), \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        assert mock_honcho.call_args.kwargs["api_key"] == "local-jwt"

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_cloud_base_url_without_version_unchanged(self):
        """A cloud base_url with no version segment must pass through untouched."""
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="cloud-key",
            base_url="https://api.honcho.dev",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        passed_base_url = mock_honcho.call_args.kwargs.get("base_url")
        assert passed_base_url == "https://api.honcho.dev", (
            f"Expected 'https://api.honcho.dev', got {passed_base_url!r}"
        )

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_cloud_base_url_with_version_stripped(self):
        """A version segment double-prefixes regardless of host, so a cloud
        base_url that ends in '/v3' must also be stripped (the SDK re-adds it)."""
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="cloud-key",
            base_url="https://api.honcho.dev/v3",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        passed_base_url = mock_honcho.call_args.kwargs.get("base_url")
        assert passed_base_url == "https://api.honcho.dev", (
            f"Expected 'https://api.honcho.dev', got {passed_base_url!r}"
        )

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    @pytest.mark.parametrize(
        "raw_url, expected",
        [
            # LAN IP self-host
            ("http://10.0.0.5:8000/v3", "http://10.0.0.5:8000"),
            ("http://192.168.1.20:38000/v3/", "http://192.168.1.20:38000"),
            # Tailscale / custom-domain self-host
            ("https://honcho.my.ts.net/v3", "https://honcho.my.ts.net"),
            ("https://honcho.lab.internal/v3", "https://honcho.lab.internal"),
            ("https://honcho.fly.dev/v3", "https://honcho.fly.dev"),
            # higher version segments are also stripped
            ("https://honcho.lab.internal/v12", "https://honcho.lab.internal"),
            # self-host without a version segment is left unchanged
            ("https://honcho.my.ts.net", "https://honcho.my.ts.net"),
            ("http://10.0.0.5:8000", "http://10.0.0.5:8000"),
        ],
    )
    def test_self_hosted_base_url_version_stripped(self, raw_url, expected):
        """Non-loopback self-hosted instances (LAN IPs, Tailscale, custom
        domains) must get the same version-segment stripping as localhost.
        Regression for #20688 recurring on any non-loopback self-host."""
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key="self-host-key",
            base_url=raw_url,
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        passed_base_url = mock_honcho.call_args.kwargs.get("base_url")
        assert passed_base_url == expected, (
            f"Expected {expected!r}, got {passed_base_url!r}"
        )

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed"
    )
    def test_local_base_url_with_trailing_slash_stripped(self):
        """base_url 'http://127.0.0.1:38000/v3/' must also be cleaned up."""
        fake_honcho = MagicMock(name="Honcho")
        cfg = HonchoClientConfig(
            api_key=None,
            base_url="http://127.0.0.1:38000/v3/",
            workspace_id="hermes",
            environment="production",
        )

        with patch("honcho.Honcho", return_value=fake_honcho) as mock_honcho, \
             patch("hermes_cli.config.load_config", return_value={}):
            get_honcho_client(cfg)

        mock_honcho.assert_called_once()
        passed_base_url = mock_honcho.call_args.kwargs.get("base_url")
        assert passed_base_url == "http://127.0.0.1:38000", (
            f"Expected 'http://127.0.0.1:38000', got {passed_base_url!r}"
        )


# ---------------------------------------------------------------------------
# Singleton reuse and deterministic close regression tests (t_e9449348)
# ---------------------------------------------------------------------------

class TestHonchoSingletonReuseAndClose:
    """Regression tests for the Honcho client lifecycle leak.

    Before the fix:
      * A no-config get_honcho_client() call after an explicit-config build
        resolved a different effective timeout (because explicit 11.0 was
        compared against the default 30.0 instead of the active config file
        value), forcing an unnecessary rebuild.
      * reset_honcho_client() dropped the cached reference without closing the
        SDK's owned sync httpx.Client, leaking FDs.
      * The lazily-created async HTTP client was never closed.
    """

    def teardown_method(self):
        reset_honcho_client()

    @pytest.mark.skipif(
        not importlib.util.find_spec("honcho"),
        reason="honcho SDK not installed",
    )
    def test_peer_fin_does_not_leave_idle_close_wait(self):
        """A peer FIN after a complete response must not remain pooled in CLOSE_WAIT."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        peer_port = listener.getsockname()[1]
        close_peer = threading.Event()
        server_errors = []

        def serve_one():
            try:
                conn, _ = listener.accept()
                with conn:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        chunk = conn.recv(4096)
                        if not chunk:
                            return
                        request += chunk
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: 2\r\n"
                        b"Connection: keep-alive\r\n\r\n{}"
                    )
                    if not close_peer.wait(timeout=2):
                        raise TimeoutError("test did not release the peer")
            except Exception as exc:  # pragma: no cover - surfaced below
                server_errors.append(exc)
            finally:
                listener.close()

        server = threading.Thread(target=serve_one, daemon=True)
        server.start()

        config = HonchoClientConfig(
            api_key="test-key",
            workspace_id="test-workspace",
            environment="production",
            base_url=f"http://127.0.0.1:{peer_port}",
            timeout=1.0,
        )
        with patch(
            "plugins.memory.honcho.client._apply_fresh_oauth_token"
        ), patch("hermes_cli.config.load_config", return_value={}):
            client = get_honcho_client(config)
            assert client._http.get("/probe") == {}

        # The response is complete while the peer is still connected. Closing
        # it now deterministically exercises an idle pooled peer-FIN rather than
        # a FIN consumed during response processing.
        close_peer.set()
        server.join(timeout=2)
        assert not server.is_alive()
        assert server_errors == []

        def peer_states():
            states = []
            for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
                with open(proc_path, encoding="ascii") as table:
                    next(table)
                    for row in table:
                        fields = row.split()
                        remote_port = int(fields[2].rsplit(":", 1)[1], 16)
                        if remote_port == peer_port:
                            states.append(fields[3])
            return states

        # Linux TCP state 08 is CLOSE_WAIT. A no-idle-pool client may already
        # have removed the socket; otherwise the peer FIN becomes observable.
        deadline = time.monotonic() + 0.5
        observed = []
        peer_close_observed = False
        while time.monotonic() < deadline:
            observed = peer_states()
            if not observed or all(state != "01" for state in observed):
                peer_close_observed = True
                break
            time.sleep(0.01)

        assert peer_close_observed, (
            f"peer close was not observed before the deadline; final states: {observed}"
        )
        assert "08" not in observed, (
            f"peer-closed Honcho connection remained pooled in CLOSE_WAIT: {observed}"
        )

    @pytest.fixture
    def real_honcho_client(self):
        """Build an offline real Honcho 2.2.0 client and close any leaked backing."""
        import asyncio
        from importlib.metadata import version
        from honcho import Honcho

        assert version("honcho-ai") == "2.2.0"
        config = HonchoClientConfig(
            api_key="test-key",
            workspace_id="test-workspace",
            environment="production",
            base_url="http://127.0.0.1:9",
            timeout=1.0,
        )
        with patch(
            "plugins.memory.honcho.client._apply_fresh_oauth_token"
        ), patch("hermes_cli.config.load_config", return_value={}):
            client = get_honcho_client(config)
        assert isinstance(client, Honcho)

        yield client

        # RED on the blocked implementation intentionally leaves the real async
        # backing open. Clean it directly without touching the lazy property.
        private = object.__getattribute__(client, "__pydantic_private__")
        async_http = private.get("_async_http")
        if async_http is not None and not async_http._client.is_closed:
            asyncio.run(async_http.close())
        http = private.get("_http")
        if http is not None and not http._client.is_closed:
            http.close()
        from plugins.memory.honcho import client as honcho_client_module
        honcho_client_module._honcho_client_slot.reset()

    def test_reset_real_sdk_does_not_create_unused_async_backing(
        self, real_honcho_client
    ):
        """Reset must not invoke Honcho 2.2.0's lazy async client property."""
        private = object.__getattribute__(
            real_honcho_client, "__pydantic_private__"
        )
        assert private["_async_http"] is None

        reset_honcho_client()

        assert private["_async_http"] is None
        assert private["_http"]._client.is_closed is True

    def test_reset_real_sdk_closes_created_async_inner_client(
        self, real_honcho_client
    ):
        """Reset must close Honcho 2.2.0's Pydantic-private async backing."""
        async_http = real_honcho_client._async_http_client
        async_inner = async_http._client
        sync_inner = real_honcho_client._http._client
        assert async_inner.is_closed is False

        reset_honcho_client()

        assert sync_inner.is_closed is True
        assert async_inner.is_closed is True

    @pytest.mark.parametrize("failure_mode", ["sync", "async"])
    def test_reset_real_sdk_detaches_and_clears_memos_on_cleanup_failure(
        self, real_honcho_client, failure_mode, caplog
    ):
        """Real SDK close failures cannot retain singleton or timeout memos."""
        from plugins.memory.honcho import client as honcho_client_module

        private = object.__getattribute__(
            real_honcho_client, "__pydantic_private__"
        )
        if failure_mode == "sync":
            http = private["_http"]
            original_close = http.close

            def failing_close():
                raise RuntimeError("real sync close exploded")
        else:
            http = real_honcho_client._async_http_client
            original_close = http.close

            async def failing_close():
                raise RuntimeError("real async close exploded")

        http.close = failing_close
        honcho_client_module._cached_timeout = 123.0
        honcho_client_module._honcho_json_timeout_memo = (object(), 456.0)
        try:
            with caplog.at_level("WARNING"):
                reset_honcho_client()
        finally:
            http.close = original_close

        assert f"real {failure_mode} close exploded" in caplog.text
        assert honcho_client_module._honcho_client_slot.peek() is None
        assert honcho_client_module._cached_timeout is None
        assert honcho_client_module._honcho_json_timeout_memo == (None, None)

    @pytest.fixture
    def fake_honcho_module(self):
        """A minimal fake Honcho SDK whose HTTP clients record close calls."""
        created = {"sync": [], "async": []}
        closed = {"sync": [], "async": []}

        class _FakeHTTPClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._client = MagicMock()
                self._client.is_closed = False
                created["sync"].append(kwargs)

            def close(self):
                if not self._client.is_closed:
                    self._client.is_closed = True
                    closed["sync"].append(self.kwargs)

        class _FakeAsyncHTTPClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._client = MagicMock()
                self._client.is_closed = False
                created["async"].append(kwargs)

            async def close(self):
                if not self._client.is_closed:
                    self._client.is_closed = True
                    closed["async"].append(self.kwargs)

        class _FakeHoncho:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._http = _FakeHTTPClient(**kwargs)
                self._async_http = None

            @property
            def _async_http_client(self):
                if self._async_http is None:
                    self._async_http = _FakeAsyncHTTPClient(**self.kwargs)
                return self._async_http

        fake_mod = types.ModuleType("honcho")
        fake_mod.Honcho = _FakeHoncho
        fake_mod.HonchoHTTPClient = _FakeHTTPClient
        fake_mod.AsyncHonchoHTTPClient = _FakeAsyncHTTPClient
        return fake_mod, created, closed

    def test_explicit_then_no_config_reuses_singleton_when_timeout_matches(self, fake_honcho_module, tmp_path, monkeypatch):
        """Explicit-config and config-less acquisitions must share one client
        when the active honcho.json resolves the same effective timeout."""
        fake_mod, created, closed = fake_honcho_module

        # Active config resolves timeout to 11.0, matching the explicit config.
        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "apiKey": "cfg-key",
            "baseUrl": "http://localhost:8000",
            "timeout": 11,
        }))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.honcho.client.resolve_config_path",
            lambda: config_file,
        )

        with patch.dict(sys.modules, {"honcho": fake_mod}), \
             patch("hermes_cli.config.load_config", return_value={}):
            c1 = get_honcho_client(HonchoClientConfig(
                api_key="explicit-key", workspace_id="ws", environment="production",
                base_url="http://localhost:8000", timeout=11.0,
            ))
            c2 = get_honcho_client()

        assert c1 is c2
        assert len(created["sync"]) == 1
        assert created["sync"][0]["timeout"] == 11.0

    def test_explicit_then_no_config_rebuilds_when_timeout_differs(self, fake_honcho_module, tmp_path, monkeypatch):
        """If the active config and explicit config disagree on timeout, the
        cached client must be deterministically closed and rebuilt."""
        fake_mod, created, closed = fake_honcho_module

        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "apiKey": "cfg-key",
            "baseUrl": "http://localhost:8000",
            "timeout": 55,
        }))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.honcho.client.resolve_config_path",
            lambda: config_file,
        )

        with patch.dict(sys.modules, {"honcho": fake_mod}), \
             patch("hermes_cli.config.load_config", return_value={}):
            c1 = get_honcho_client(HonchoClientConfig(
                api_key="explicit-key", workspace_id="ws", environment="production",
                base_url="http://localhost:8000", timeout=11.0,
            ))
            c2 = get_honcho_client()

        assert c1 is not c2
        assert len(created["sync"]) == 2
        # Old client must be closed before replacement.
        assert len(closed["sync"]) == 1
        assert closed["sync"][0] is c1._http.kwargs

    def test_reset_closes_sync_client(self, fake_honcho_module):
        """reset_honcho_client() must close the SDK's owned sync HTTP client."""
        fake_mod, created, closed = fake_honcho_module

        with patch.dict(sys.modules, {"honcho": fake_mod}), \
             patch("hermes_cli.config.load_config", return_value={}):
            c1 = get_honcho_client(HonchoClientConfig(
                api_key="k", workspace_id="ws", environment="production",
                timeout=11.0,
            ))
            reset_honcho_client()

        assert len(closed["sync"]) == 1
        assert closed["sync"][0] is c1._http.kwargs

    def test_reset_closes_async_client(self, fake_honcho_module):
        """reset_honcho_client() must close the lazily-created async HTTP client."""
        fake_mod, created, closed = fake_honcho_module

        import asyncio
        with patch.dict(sys.modules, {"honcho": fake_mod}), \
             patch("hermes_cli.config.load_config", return_value={}):
            c1 = get_honcho_client(HonchoClientConfig(
                api_key="k", workspace_id="ws", environment="production",
                timeout=11.0,
            ))
            # Force lazy async creation.
            _ = c1._async_http_client
            reset_honcho_client()

        assert len(closed["sync"]) == 1
        assert len(closed["async"]) == 1

    @pytest.mark.parametrize("failure_mode", ["access", "close"])
    def test_reset_avoids_lazy_async_allocation_and_detaches_on_cleanup_failure(
        self, failure_mode
    ):
        """Cleanup failures must not allocate async state or retain the singleton."""
        built = []
        async_created = []

        class _FailingHTTPClient:
            _owns_client = True

            def close(self):
                if failure_mode == "close":
                    raise RuntimeError("sync close exploded")

        class _FakeAsyncHTTPClient:
            _owns_client = True

            async def close(self):
                pass

        class _FakeHoncho:
            def __init__(self, **kwargs):
                self._http = _FailingHTTPClient()
                self._async_http = None
                built.append(self)

            def __getattribute__(self, name):
                if name == "_http" and failure_mode == "access":
                    raise RuntimeError("sync cleanup access exploded")
                return object.__getattribute__(self, name)

            @property
            def _async_http_client(self):
                async_created.append(True)
                self._async_http = _FakeAsyncHTTPClient()
                return self._async_http

        fake_mod = types.ModuleType("honcho")
        fake_mod.Honcho = _FakeHoncho

        try:
            with patch.dict(sys.modules, {"honcho": fake_mod}), \
                 patch("hermes_cli.config.load_config", return_value={}):
                config = HonchoClientConfig(
                    api_key="k", workspace_id="ws", environment="production",
                    timeout=11.0,
                )
                first = get_honcho_client(config)
                reset_honcho_client()
                second = get_honcho_client(config)

            assert async_created == []
            assert second is not first
            assert built == [first, second]
        finally:
            # Keep a deliberately broken cleanup fake from contaminating teardown.
            from plugins.memory.honcho import client as honcho_client_module
            honcho_client_module._honcho_client_slot.reset()

    def test_running_loop_async_close_failure_is_observed(
        self, fake_honcho_module, caplog
    ):
        """A scheduled close failure must be retrieved and logged, not orphaned."""
        import asyncio

        fake_mod, _, _ = fake_honcho_module

        async def exercise():
            with patch.dict(sys.modules, {"honcho": fake_mod}), \
                 patch("hermes_cli.config.load_config", return_value={}):
                client = get_honcho_client(HonchoClientConfig(
                    api_key="k", workspace_id="ws", environment="production",
                    timeout=11.0,
                ))
                async_http = client._async_http_client

                async def failing_close():
                    raise RuntimeError("close exploded")

                async_http.close = failing_close
                reset_honcho_client()
                await asyncio.sleep(0)

        with caplog.at_level("WARNING"):
            asyncio.run(exercise())

        assert "Honcho async client close failed" in caplog.text
        assert "close exploded" in caplog.text

    def test_timeout_change_triggers_close_and_rebuild(self, fake_honcho_module, tmp_path, monkeypatch):
        """Changing the effective timeout after the client is cached must close
        the old sync client before rebuilding."""
        fake_mod, created, closed = fake_honcho_module

        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "apiKey": "cfg-key",
            "baseUrl": "http://localhost:8000",
            "timeout": 10,
        }))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.honcho.client.resolve_config_path",
            lambda: config_file,
        )

        with patch.dict(sys.modules, {"honcho": fake_mod}), \
             patch("hermes_cli.config.load_config", return_value={}):
            c1 = get_honcho_client()
            assert len(created["sync"]) == 1
            # Mutate the active config file so the next resolution differs.
            config_file.write_text(json.dumps({
                "apiKey": "cfg-key",
                "baseUrl": "http://localhost:8000",
                "timeout": 20,
            }))
            c2 = get_honcho_client()

        assert c1 is not c2
        assert len(created["sync"]) == 2
        assert len(closed["sync"]) == 1
        assert closed["sync"][0] is c1._http.kwargs

    def test_config_less_identity_reuse(self, fake_honcho_module, tmp_path, monkeypatch):
        """Two consecutive no-config acquisitions must return the same client."""
        fake_mod, created, closed = fake_honcho_module

        config_file = tmp_path / "honcho.json"
        config_file.write_text(json.dumps({
            "apiKey": "cfg-key",
            "baseUrl": "http://localhost:8000",
            "timeout": 30,
        }))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.honcho.client.resolve_config_path",
            lambda: config_file,
        )

        with patch.dict(sys.modules, {"honcho": fake_mod}), \
             patch("hermes_cli.config.load_config", return_value={}):
            c1 = get_honcho_client()
            c2 = get_honcho_client()

        assert c1 is c2
        assert len(created["sync"]) == 1
        assert len(closed["sync"]) == 0
