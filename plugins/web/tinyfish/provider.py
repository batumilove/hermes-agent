"""TinyFish web search + content extraction provider.

TinyFish exposes free Search and Fetch APIs that map directly onto Hermes'
``web_search`` and ``web_extract`` tool contracts. The metered TinyFish Agent
and Browser APIs are intentionally not surfaced here.

Env vars::

    TINYFISH_API_KEY=...          # https://agent.tinyfish.ai/api-keys
    TINYFISH_SEARCH_URL=...       # optional override
    TINYFISH_FETCH_URL=...        # optional override
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import httpx

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_DEFAULT_SEARCH_URL = "https://api.search.tinyfish.ai"
_DEFAULT_FETCH_URL = "https://api.fetch.tinyfish.ai"


def _get_api_key() -> str:
    api_key = os.getenv("TINYFISH_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "TINYFISH_API_KEY environment variable not set. "
            "Create one at https://agent.tinyfish.ai/api-keys"
        )
    return api_key


def _headers(*, json_request: bool = False) -> Dict[str, str]:
    headers = {"X-API-Key": _get_api_key()}
    if json_request:
        headers["Content-Type"] = "application/json"
    return headers


def _normalize_search_results(response: Dict[str, Any], limit: int) -> Dict[str, Any]:
    web_results = []
    raw_results = response.get("results") or response.get("data") or []
    if isinstance(raw_results, dict):
        raw_results = raw_results.get("web") or raw_results.get("results") or []

    for i, result in enumerate(raw_results[:limit]):
        if not isinstance(result, dict):
            continue
        web_results.append(
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "description": result.get("snippet", "") or result.get("description", "") or result.get("content", ""),
                "position": result.get("position") or i + 1,
            }
        )
    return {"success": True, "data": {"web": web_results}}


def _normalize_fetch_documents(response: Dict[str, Any], fallback_urls: List[str]) -> List[Dict[str, Any]]:
    documents: List[Dict[str, Any]] = []

    for result in response.get("results", []) or []:
        if not isinstance(result, dict):
            continue
        url = result.get("url") or result.get("final_url") or ""
        final_url = result.get("final_url") or result.get("finalUrl") or ""
        title = result.get("title", "")
        content = result.get("text", "") or result.get("content", "") or result.get("markdown", "") or ""
        documents.append(
            {
                "url": url,
                "title": title,
                "content": content,
                "raw_content": content,
                "metadata": {
                    "sourceURL": url,
                    **({"finalURL": final_url} if final_url else {}),
                    "title": title,
                    **({"description": result["description"]} if result.get("description") else {}),
                    **({"language": result["language"]} if result.get("language") else {}),
                },
            }
        )

    for error in response.get("errors", []) or []:
        if isinstance(error, dict):
            url = error.get("url", "")
            message = error.get("error") or error.get("message") or "fetch failed"
        else:
            url = str(error)
            message = "fetch failed"
        documents.append(
            {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": message,
                "metadata": {"sourceURL": url},
            }
        )

    if not documents:
        for url in fallback_urls:
            documents.append(
                {
                    "url": url,
                    "title": "",
                    "content": "",
                    "raw_content": "",
                    "error": "No content returned by TinyFish Fetch",
                    "metadata": {"sourceURL": url},
                }
            )
    return documents


class TinyFishWebSearchProvider(WebSearchProvider):
    """TinyFish Search + Fetch provider."""

    @property
    def name(self) -> str:
        return "tinyfish"

    @property
    def display_name(self) -> str:
        return "TinyFish"

    def is_available(self) -> bool:
        return bool(os.getenv("TINYFISH_API_KEY", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            search_url = os.getenv("TINYFISH_SEARCH_URL", _DEFAULT_SEARCH_URL).rstrip("/")
            logger.info("TinyFish search: '%s' (limit=%d)", query, limit)
            response = httpx.get(
                search_url,
                params={"query": query},
                headers=_headers(),
                timeout=30,
            )
            response.raise_for_status()
            return _normalize_search_results(response.json(), limit)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("TinyFish search error: %s", exc)
            return {"success": False, "error": f"TinyFish search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return [{"url": u, "title": "", "content": "", "raw_content": "", "error": "Interrupted"} for u in urls]

            fetch_url = os.getenv("TINYFISH_FETCH_URL", _DEFAULT_FETCH_URL).rstrip("/")
            requested_format = (kwargs.get("format") or "markdown").lower()
            if requested_format not in {"markdown", "html", "json"}:
                requested_format = "markdown"

            logger.info("TinyFish fetch: %d URL(s)", len(urls))
            response = httpx.post(
                fetch_url,
                json={"urls": urls, "format": requested_format},
                headers=_headers(json_request=True),
                timeout=60,
            )
            response.raise_for_status()
            return _normalize_fetch_documents(response.json(), urls)
        except ValueError as exc:
            return [{"url": u, "title": "", "content": "", "raw_content": "", "error": str(exc)} for u in urls]
        except Exception as exc:  # noqa: BLE001
            logger.warning("TinyFish fetch error: %s", exc)
            return [
                {"url": u, "title": "", "content": "", "raw_content": "", "error": f"TinyFish fetch failed: {exc}"}
                for u in urls
            ]

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "TinyFish",
            "badge": "free",
            "tag": "Free Search + Fetch APIs; Agent/Browser are not enabled by this backend.",
            "env_vars": [
                {
                    "key": "TINYFISH_API_KEY",
                    "prompt": "TinyFish API key",
                    "url": "https://agent.tinyfish.ai/api-keys",
                },
            ],
        }
