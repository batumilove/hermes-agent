"""Regression tests for the gateway heartbeat writer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner
from gateway.status import read_heartbeat, heartbeat_age_seconds


class _SuccessfulAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.DISCORD)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.mark.asyncio
async def test_heartbeat_writer_starts_before_platforms_and_writes_file(
    monkeypatch, tmp_path
):
    """The heartbeat task must start before platform adapters connect and write a heartbeat file."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.DISCORD: PlatformConfig(enabled=True, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())

    ok = await runner.start()

    assert ok is True
    # Heartbeat task should exist and be actively running
    assert runner._heartbeat_task is not None

    # Give the heartbeat writer a moment to write the file
    for _ in range(50):
        if (tmp_path / "gateway.heartbeat").exists():
            break
        await asyncio.sleep(0.05)

    heartbeat = read_heartbeat()
    assert heartbeat is not None
    assert heartbeat["pid"] > 0
    assert "last_heartbeat_at" in heartbeat
    age = heartbeat_age_seconds()
    assert age is not None
    assert age < 5.0

    await runner.stop()


@pytest.mark.asyncio
async def test_heartbeat_writer_cancelled_on_shutdown(monkeypatch, tmp_path):
    """The heartbeat task must be cancelled when the gateway stops."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.DISCORD: PlatformConfig(enabled=True, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())

    await runner.start()
    heartbeat_task = runner._heartbeat_task
    assert heartbeat_task is not None
    assert not heartbeat_task.done()

    await runner.stop()

    assert heartbeat_task.done()
    assert runner._heartbeat_task is None


@pytest.mark.asyncio
async def test_heartbeat_age_gauge_exposes_staleness(monkeypatch, tmp_path):
    """heartbeat_age_seconds() should return the age of the last heartbeat."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="***")},
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())

    await runner.start()
    # Wait for at least one heartbeat to be written
    for _ in range(50):
        if read_heartbeat() is not None:
            break
        await asyncio.sleep(0.05)

    age = heartbeat_age_seconds()
    assert age is not None
    assert age >= 0.0

    await runner.stop()
