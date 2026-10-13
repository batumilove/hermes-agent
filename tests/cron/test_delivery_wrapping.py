"""Cron verbatim script delivery and live media routing contracts."""

from unittest.mock import AsyncMock, MagicMock, patch
from cron.scheduler import _deliver_result


class TestDeliverResultWrapping:
    """Verify that cron deliveries are wrapped with header/footer and no longer mirrored."""

    def _safe_media_path(self, tmp_path, monkeypatch, name, data=b"media"):
        root = tmp_path / "media-cache"
        media_file = root / name
        media_file.parent.mkdir(parents=True, exist_ok=True)
        media_file.write_bytes(data)
        monkeypatch.setattr(
            "gateway.platforms.base.MEDIA_DELIVERY_SAFE_ROOTS",
            (root,),
        )
        return media_file.resolve()

    def test_relay_fronted_home_uses_relay_config_and_live_adapter(self, monkeypatch, tmp_path):
        """Persisted Slack home survives restart without native Slack config."""
        from concurrent.futures import Future

        from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig

        relay = MagicMock()
        relay.fronts_platform.side_effect = lambda platform: platform == Platform.SLACK
        relay.send_for_platform = AsyncMock(return_value=MagicMock(success=True))
        relay.send_voice = AsyncMock(return_value=MagicMock(success=True))
        relay.supports_inchannel_continuable = False
        # Not a real RelayAdapter: keep the auto-created accessor from
        # shadowing the scalar False (MagicMock fabricates truthy callables).
        relay.supports_inchannel_continuable_for_platform = None

        config = GatewayConfig(
            platforms={
                Platform.RELAY: PlatformConfig(enabled=True),
                Platform.SLACK: PlatformConfig(
                    enabled=False,
                    home_channel=HomeChannel(
                        platform=Platform.SLACK,
                        chat_id="D123",
                        name="Owner DM",
                        user_id="U123",
                    ),
                ),
            },
        )
        loop = MagicMock()
        loop.is_running.return_value = True

        def fake_run_coro(coro, _loop):
            import asyncio as _asyncio

            future = Future()
            try:
                future.set_result(_asyncio.run(coro))
            except BaseException as exc:
                future.set_exception(exc)
            return future

        standalone_send = AsyncMock(return_value={"success": True})
        media_path = self._safe_media_path(tmp_path, monkeypatch, "relay-voice.mp3")
        monkeypatch.setenv("SLACK_HOME_CHANNEL", "D123")
        job = {
            "id": "relay-cron",
            "deliver": "slack",
        }

        with (
            patch("gateway.config.load_gateway_config", return_value=config),
            patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}),
            patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro),
            patch("tools.send_message_tool._send_to_platform", new=standalone_send),
        ):
            result = _deliver_result(
                job,
                f"scheduled result\nMEDIA:{media_path}",
                adapters={Platform.RELAY: relay},
                loop=loop,
            )

        assert result is None
        relay.send_for_platform.assert_awaited_once()
        args = relay.send_for_platform.await_args.args
        assert args[:3] == (Platform.SLACK, "D123", "scheduled result")
        assert relay.send_for_platform.await_args.kwargs["metadata"]["user_id"] == "U123"
        relay.send_voice.assert_awaited_once()
        media_metadata = relay.send_voice.await_args.kwargs["metadata"]
        assert media_metadata["_relay_logical_platform"] == "slack"
        assert media_metadata["user_id"] == "U123"
        standalone_send.assert_not_awaited()

    def test_live_adapter_sends_media_as_attachments(self, tmp_path, monkeypatch):
        """When a live adapter is available, MEDIA files should be sent as native
        platform attachments (e.g., Discord voice, Telegram audio) rather than
        as literal 'MEDIA:/path' text."""
        from gateway.config import Platform
        from concurrent.futures import Future
        media_path = self._safe_media_path(tmp_path, monkeypatch, "cron-voice.mp3")

        adapter = AsyncMock()
        adapter.send.return_value = MagicMock(success=True)
        adapter.send_voice.return_value = MagicMock(success=True)

        pconfig = MagicMock()
        pconfig.enabled = True
        mock_cfg = MagicMock()
        mock_cfg.platforms = {Platform.DISCORD: pconfig}

        loop = MagicMock()
        loop.is_running.return_value = True

        # run_coroutine_threadsafe returns concurrent.futures.Future (has timeout kwarg)
        def fake_run_coro(coro, _loop):
            # Actually run the routed coroutine (router._deliver_to_platform)
            # so the underlying adapter.send is invoked, then wrap the real
            # result in a completed Future (matching run_coroutine_threadsafe).
            import asyncio as _asyncio
            future = Future()
            try:
                future.set_result(_asyncio.run(coro))
            except BaseException as _e:
                future.set_exception(_e)
            return future

        job = {
            "id": "tts-job",
            "deliver": "origin",
            "origin": {"platform": "discord", "chat_id": "9876"},
        }

        with patch("gateway.config.load_gateway_config", return_value=mock_cfg), \
             patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}), \
             patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro):
            _deliver_result(
                job,
                f"Here is TTS\nMEDIA:{media_path}",
                adapters={Platform.DISCORD: adapter},
                loop=loop,
            )

        # Text should be sent without the MEDIA tag
        adapter.send.assert_called_once()
        text_sent = adapter.send.call_args[0][1]
        assert "MEDIA:" not in text_sent
        assert "Here is TTS" in text_sent

        # Audio file should be sent as a voice attachment
        adapter.send_voice.assert_called_once()
        voice_call = adapter.send_voice.call_args
        assert voice_call[1]["audio_path"] == str(media_path)
