"""Contract: the per-reasoning-delta hot path must not load config each call.

Evidence (2026-08-18 gateway faulthandler dump, PID 3046933): 15 threads
convoyed on hermes_cli.config._CONFIG_LOCK while the holder was deep-copying
the whole config inside _load_config_impl; 14 of the waiters entered via
plugin_stream_hooks.stream_reasoning_deltas_enabled(), which ran load_config()
for every reasoning delta of every concurrent stream. The convoy starved the
gateway liveness probe (watchdog exit 75 / TEMPFAIL restarts).

Invariant tested here: repeated calls to stream_reasoning_deltas_enabled()
without a config change must not repeatedly enter load_config().
A config-file change must still be picked up (staleness bound: next call).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import plugin_stream_hooks  # noqa: E402


def test_reasoning_delta_flag_does_not_reload_config_per_call(tmp_path, monkeypatch):
    # Isolated HERMES_HOME so get_config_path() points at a writable file.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("plugins:\n  stream_reasoning_deltas: true\n")

    # Reset any module-level cache left by other tests.
    monkeypatch.setattr(plugin_stream_hooks, "_STREAM_REASONING_CACHE", None, raising=False)

    calls = {"n": 0}
    import hermes_cli.config as config_mod

    real_load = config_mod.load_config

    def counting_load(*a, **kw):
        calls["n"] += 1
        return real_load(*a, **kw)

    monkeypatch.setattr(config_mod, "load_config", counting_load)
    monkeypatch.setattr(
        plugin_stream_hooks, "_load_config_once", counting_load, raising=False
    )

    assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is True
    first = calls["n"]
    assert first >= 1  # first call may prime the cache

    # Hot path: 1000 deltas — must not trigger 1000 config loads.
    for _ in range(1000):
        assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is True
    assert calls["n"] == first, (
        f"config reloaded per reasoning delta: {calls['n']} loads "
        f"after {first} (lock-convoy hot path not fixed)"
    )

    # Staleness bound: after a config edit, the next call reflects the new value.
    cfg.write_text("plugins:\n  stream_reasoning_deltas: false\n")
    import os

    os.utime(cfg, (cfg.stat().st_atime + 5, cfg.stat().st_mtime + 5))
    assert plugin_stream_hooks.stream_reasoning_deltas_enabled() is False
