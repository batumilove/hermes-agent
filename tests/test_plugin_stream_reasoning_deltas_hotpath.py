"""RED/GREEN: stream_reasoning_deltas_enabled must not take the config write path.

Regression for the gateway watchdog kills (exit 75, 2026-08-18): the hot
per-reasoning-delta call site ran load_config(), serializing 16 threads on
_CONFIG_LOCK behind a 285KB deepcopy and starving the event loop. The fix
routes it through load_config_readonly() (no deepcopy, no lock hold beyond
cache read).

Contract: for unchanged config signatures, repeated invocations must not
acquire the writer-config path (no deepcopy of the cached config) and must
return the same value as load_config().
"""

import os
import tempfile
from unittest import mock

import pytest


@pytest.fixture
def temp_home(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("HERMES_HOME", d)
        monkeypatch.setattr("hermes_cli.config._LOAD_CONFIG_CACHE", {})
        yield d


def _write_cfg(home: str, enabled: bool) -> None:
    with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(f"plugins:\n  stream_reasoning_deltas: {str(enabled).lower()}\n")


def test_hot_path_skips_deepcopy(temp_home):
    from agent import plugin_stream_hooks
    from hermes_cli import config as config_mod

    _write_cfg(temp_home, True)
    assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is True

    # Prime cache, then assert the hot path never deepcopies the cached value.
    deepcopy_calls = []
    real_deepcopy = config_mod.copy.deepcopy

    def spy_deepcopy(obj, *a, **k):
        deepcopy_calls.append(obj)
        return real_deepcopy(obj, *a, **k)

    with mock.patch.object(config_mod.copy, "deepcopy", side_effect=spy_deepcopy):
        assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is True
    assert not deepcopy_calls, (
        "stream_reasoning_deltas_enabled() deepcopy'd the config on the hot path; "
        "use load_config_readonly()"
    )


def test_value_matches_load_config_both_settings(temp_home):
    from agent import plugin_stream_hooks
    from hermes_cli import config as config_mod

    for enabled in (True, False):
        _write_cfg(temp_home, enabled)
        config_mod._LOAD_CONFIG_CACHE.clear()
        cfg = config_mod.load_config()
        expected = bool(
            config_mod.cfg_get(cfg, "plugins", "stream_reasoning_deltas", default=False)
        )
        assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is expected


def test_repeated_calls_do_not_reparse_or_grow(temp_home):
    from agent import plugin_stream_hooks
    from hermes_cli import config as config_mod

    _write_cfg(temp_home, True)
    plugin_stream_hooks.stream_reasoning_deltas_enabled()

    # Cache is primed; the hot path must serve from cache — no YAML re-parse.
    parses = []
    real_parse = config_mod.fast_safe_load

    def spy_parse(f, *a, **k):
        parses.append(1)
        return real_parse(f, *a, **k)

    with mock.patch.object(config_mod, "fast_safe_load", side_effect=spy_parse):
        for _ in range(50):
            assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is True
    assert not parses, "hot path re-parsed config.yaml per call"
