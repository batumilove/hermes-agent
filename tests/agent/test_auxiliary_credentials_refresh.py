"""Exact failed-bearer auxiliary refresh/cache ownership regressions."""
import pytest
from tests.agent import test_auxiliary_client as _suite
_clean_env = _suite._clean_env

class TestRetainedAuxiliaryCredentials:

    def test_runtime_refresh_preserves_failed_bearer_when_pool_already_rotated(self):
        """A reloaded pool must not replace the failed-token identity with its fresh key."""
        from agent.auxiliary_client import _resolve_nous_runtime_api

        fresh_entry = _suite.SimpleNamespace(id="nous-stable-id",
            agent_key="peer-rotated-key",
            agent_key_expires_at=None,
            access_token="peer-rotated-access",
            expires_at=None,
            scope="inference:invoke",
            base_url="https://inference-api.nousresearch.com/v1",
        )

        class _Pool:
            hint = None

            def has_credentials(self):
                return True

            def select(self):
                return fresh_entry

            def try_refresh_current(self):
                raise AssertionError("must not refresh current peer-rotated credential")

            def try_refresh_matching(self, api_key_hint=None, credential_id=None):
                self.hint = api_key_hint

        pool = _Pool()
        with _suite.patch("agent.auxiliary_client.load_pool", return_value=pool), _suite.patch(
            "hermes_cli.auth.resolve_nous_runtime_credentials",
            return_value={
                "api_key": "peer-rotated-key",
                "base_url": "https://inference-api.nousresearch.com/v1",
            },
        ) as mock_singleton:
            result = _resolve_nous_runtime_api(
                force_refresh=True,
                stale_access_token="jwt-that-actually-401d",
            )

        assert result == (
            "peer-rotated-key",
            "https://inference-api.nousresearch.com/v1",
        )
        assert pool.hint == "jwt-that-actually-401d"
        mock_singleton.assert_called_once_with(
            timeout_seconds=15,
            force_refresh=True,
            stale_access_token="jwt-that-actually-401d",
        )

    @pytest.mark.parametrize("requested_model", ["requested-model", None])
    def test_nous_refresh_replaces_original_task_and_requested_model_cache_key(
        self, requested_model
    ):
        """Recovery must replace the exact cache slot used by the failed call."""
        import agent.auxiliary_client as aux

        stale_client = _suite.MagicMock()
        fresh_client = _suite.MagicMock()
        runtime = {
            "provider": "nous",
            "model": "runtime-default-model",
            "base_url": "https://inference-api.nousresearch.com/v1",
        }

        with _suite.patch(
            "agent.auxiliary_client._pool_cache_hint", return_value=None
        ), _suite.patch(
            "agent.auxiliary_client._resolve_nous_runtime_api",
            return_value=(
                "fresh-agent-key",
                "https://inference-api.nousresearch.com/v1",
            ),
        ), _suite.patch(
            "agent.auxiliary_client._create_openai_client",
            return_value=fresh_client,
        ):
            aux.shutdown_cached_clients()
            original_key = aux._client_cache_key(
                "auto",
                async_mode=False,
                base_url="https://inference-api.nousresearch.com/v1",
                api_key="jwt-that-just-401d",
                main_runtime=runtime,
                task="compression",
                model=requested_model,
            )
            aux._client_cache[original_key] = (
                stale_client,
                "resolved-nous-model",
                None,
            )
            try:
                client, model = aux._refresh_nous_auxiliary_client(
                    cache_provider="auto",
                    model="resolved-nous-model",
                    lookup_model=requested_model,
                    lookup_task="compression",
                    async_mode=False,
                    base_url="https://inference-api.nousresearch.com/v1",
                    api_key="jwt-that-just-401d",
                    main_runtime=runtime,
                )

                assert client is fresh_client
                assert model == "resolved-nous-model"
                assert aux._client_cache[original_key][0] is fresh_client
                assert len(aux._client_cache) == 1
            finally:
                aux.shutdown_cached_clients()

    def test_nous_fallback_401_passes_exact_failed_bearer(self):
        """A Nous fallback must identify the token that actually received 401."""
        from agent.auxiliary_client import _call_fallback_candidate_sync

        stale_fb = _suite.MagicMock()
        stale_fb.api_key = "jwt-that-just-401d"
        stale_fb.base_url = "https://inference-api.nousresearch.com/v1"
        stale_fb.chat.completions.create.side_effect = _suite._AuxAuth401("Invalid bearer token")

        fresh_fb = _suite.MagicMock()
        fresh_fb.base_url = "https://inference-api.nousresearch.com/v1"
        fresh_fb.chat.completions.create.return_value = _suite._DummyResponse("fresh-nous-fallback")

        with _suite.patch(
            "agent.auxiliary_client._refresh_provider_credentials",
            return_value=True,
        ) as mock_refresh, _suite.patch(
            "agent.auxiliary_client._get_cached_client",
            return_value=(fresh_fb, "Hermes-4-405B"),
        ):
            result = _call_fallback_candidate_sync(
                stale_fb,
                "Hermes-4-405B",
                "nous",
                task="compression",
                messages=[{"role": "user", "content": "summarize"}],
                temperature=None,
                max_tokens=256,
                tools=None,
                effective_timeout=30.0,
                effective_extra_body={},
                reasoning_config=None,
            )

        assert result.choices[0].message.content == "fresh-nous-fallback"
        mock_refresh.assert_called_once_with(
            "nous", failed_api_key="jwt-that-just-401d"
        )

    @pytest.mark.asyncio
    async def test_async_call_llm_retries_nous_with_original_cache_runtime(self):
        """Async Nous refresh must replace the same runtime-scoped cache entry."""
        class _Auth401(Exception):
            status_code = 401

        stale_client = _suite.MagicMock()
        stale_client.base_url = "https://inference-api.nousresearch.com/v1"
        stale_client.api_key = "async-jwt-that-just-401d"
        stale_client.chat.completions.create = _suite.AsyncMock(
            side_effect=_Auth401("stale nous key")
        )

        fresh_client = _suite.MagicMock()
        fresh_client.base_url = "https://inference-api.nousresearch.com/v1"
        fresh_client.chat.completions.create = _suite.AsyncMock(return_value={"ok": True})
        runtime = {
            "provider": "nous",
            "model": "nous-model",
            "base_url": "https://inference-api.nousresearch.com/v1",
            "api_key": None,
        }

        with _suite.patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=("nous", "nous-model", None, None, None),
        ), _suite.patch(
            "agent.auxiliary_client._get_cached_client",
            return_value=(stale_client, "nous-model"),
        ), _suite.patch(
            "agent.auxiliary_client._refresh_nous_auxiliary_client",
            return_value=(fresh_client, "nous-model"),
        ) as mock_refresh, _suite.patch(
            "agent.auxiliary_client._validate_llm_response",
            side_effect=lambda resp, _task, **_kw: resp,
        ):
            result = await _suite.async_call_llm(
                task="compression",
                provider="auto",
                main_runtime=runtime,
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result == {"ok": True}
        assert mock_refresh.call_args.kwargs["main_runtime"] == {
            "provider": "nous",
            "model": "nous-model",
            "base_url": "https://inference-api.nousresearch.com/v1",
        }
        assert mock_refresh.call_args.kwargs["stale_access_token"] == (
            "async-jwt-that-just-401d"
        )

    @pytest.mark.asyncio
    async def test_async_nous_fallback_401_passes_exact_failed_bearer(self):
        """Async fallback recovery preserves the bearer rejected upstream."""
        from agent.auxiliary_client import _call_fallback_candidate_async

        stale_fb = _suite.MagicMock()
        stale_fb.api_key = "async-jwt-that-just-401d"
        stale_fb.base_url = "https://inference-api.nousresearch.com/v1"
        stale_fb.chat.completions.create = _suite.AsyncMock(
            side_effect=_suite._AuxAuth401("Invalid bearer token")
        )

        fresh_fb = _suite.MagicMock()
        fresh_fb.base_url = "https://inference-api.nousresearch.com/v1"
        fresh_fb.chat.completions.create = _suite.AsyncMock(
            return_value=_suite._DummyResponse("fresh-async-nous-fallback")
        )

        with _suite.patch(
            "agent.auxiliary_client._refresh_provider_credentials",
            return_value=True,
        ) as mock_refresh, _suite.patch(
            "agent.auxiliary_client._get_cached_client",
            return_value=(fresh_fb, "Hermes-4-405B"),
        ):
            result = await _call_fallback_candidate_async(
                stale_fb,
                "Hermes-4-405B",
                "nous",
                task="compression",
                messages=[{"role": "user", "content": "summarize"}],
                temperature=None,
                max_tokens=256,
                tools=None,
                effective_timeout=30.0,
                effective_extra_body={},
                reasoning_config=None,
            )

        assert result.choices[0].message.content == "fresh-async-nous-fallback"
        mock_refresh.assert_called_once_with(
            "nous", failed_api_key="async-jwt-that-just-401d"
        )


class TestNousTagsScoping:
    def test_tags_injected_when_provider_is_nous(self, monkeypatch):
        import agent.auxiliary_client as aux

        monkeypatch.setattr(aux, "auxiliary_is_nous", False)

        kwargs = aux._build_call_kwargs(
            provider="nous",
            model="hermes-4",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert kwargs["extra_body"]["tags"] == aux._nous_portal_tags()

    def test_tags_not_injected_for_gemini_when_main_is_nous(self, monkeypatch):
        import agent.auxiliary_client as aux

        monkeypatch.setattr(aux, "auxiliary_is_nous", True)

        kwargs = aux._build_call_kwargs(
            provider="gemini",
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert "extra_body" not in kwargs
