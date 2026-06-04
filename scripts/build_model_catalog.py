#!/usr/bin/env python3
"""Build the Hermes Model Catalog — a centralized JSON manifest of curated models.

This script reads Hermes' in-repo curated model lists and provider metadata,
then writes the JSON manifest fetched by the CLI at runtime. Publishing the
catalog through the docs site lets maintainers update curated picker fallback
lists without shipping a Hermes release.

The runtime fetcher falls back to the same in-repo hardcoded lists if the
manifest is unreachable, so this script is a convenience for keeping the
manifest in sync — not a source of truth.

Usage::

    python scripts/build_model_catalog.py

Output: ``website/static/api/model-catalog.json``

Live URL (after ``deploy-site.yml`` runs on merge to main):
``https://hermes-agent.nousresearch.com/docs/api/model-catalog.json``
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Ensure HERMES_HOME is set for imports that touch it at module level.
os.environ.setdefault("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes"))

from hermes_cli.auth import PROVIDER_REGISTRY  # noqa: E402
from hermes_cli.models import OPENROUTER_MODELS, _PROVIDER_MODELS  # noqa: E402
from hermes_cli.providers import HERMES_OVERLAYS  # noqa: E402
from providers import get_provider_profile  # noqa: E402

OUTPUT_PATH = os.path.join(REPO_ROOT, "website", "static", "api", "model-catalog.json")
CATALOG_VERSION = 1


def _provider_metadata(provider: str, *, note: str = "") -> dict[str, Any]:
    """Build non-secret provider metadata for the public model catalog."""
    auth = PROVIDER_REGISTRY.get(provider)
    profile = get_provider_profile(provider)
    overlay = HERMES_OVERLAYS.get(provider)

    display_name = (
        (auth.name if auth else "")
        or (profile.display_name if profile else "")
        or provider
    )
    description = (profile.description if profile else "") or ""
    signup_url = (profile.signup_url if profile else "") or ""
    auth_type = (
        (auth.auth_type if auth else "")
        or (overlay.auth_type if overlay else "")
        or (profile.auth_type if profile else "")
        or "api_key"
    )
    env_vars: list[str] = []
    if auth and auth.api_key_env_vars:
        env_vars.extend(auth.api_key_env_vars)
    elif profile and profile.env_vars:
        env_vars.extend(v for v in profile.env_vars if not v.endswith("_URL"))
    elif overlay and overlay.extra_env_vars:
        env_vars.extend(overlay.extra_env_vars)

    base_url_env_var = (
        (auth.base_url_env_var if auth else "")
        or (overlay.base_url_env_var if overlay else "")
        or ""
    )
    base_url = (
        (auth.inference_base_url if auth else "")
        or (overlay.base_url_override if overlay else "")
        or (profile.base_url if profile else "")
    )
    transport = (overlay.transport if overlay else "") or ""

    metadata: dict[str, Any] = {
        "display_name": display_name,
        "auth_type": auth_type,
        "env_vars": sorted(dict.fromkeys(env_vars)),
    }
    if description:
        metadata["description"] = description
    if signup_url:
        metadata["signup_url"] = signup_url
    if base_url:
        metadata["base_url"] = base_url
    if base_url_env_var:
        metadata["base_url_env_var"] = base_url_env_var
    if transport:
        metadata["transport"] = transport
    if overlay and overlay.is_aggregator:
        metadata["aggregator"] = True
    if provider not in _PROVIDER_MODELS and provider != "openrouter":
        metadata["dynamic_models"] = True
    if note:
        metadata["note"] = note
    return metadata


def _model_entries(provider: str) -> list[dict[str, Any]]:
    return [{"id": mid} for mid in _PROVIDER_MODELS.get(provider, [])]


def build_catalog() -> dict:
    provider_names = set(_PROVIDER_MODELS)
    provider_names.update(k for k, v in PROVIDER_REGISTRY.items() if getattr(v, "id", k) == k)
    provider_names.update(HERMES_OVERLAYS)
    provider_names.add("openrouter")

    providers: dict[str, dict[str, Any]] = {}
    for provider in sorted(provider_names):
        if provider == "openrouter":
            continue
        providers[provider] = {
            "metadata": _provider_metadata(
                provider,
                note="Curated fallback list generated from hermes_cli.models._PROVIDER_MODELS.",
            ),
            "models": _model_entries(provider),
        }

    providers["openrouter"] = {
        "metadata": _provider_metadata(
            "openrouter",
            note=(
                "Descriptions drive picker badges. Live /api/v1/models "
                "filters curated ids by tool-calling support and free pricing."
            ),
        ) | {"display_name": "OpenRouter", "aggregator": True},
        "models": [
            {"id": mid, "description": desc}
            for mid, desc in OPENROUTER_MODELS
        ],
    }
    providers["nous"] = {
        "metadata": _provider_metadata(
            "nous",
            note=(
                "Free-tier gating is determined live via Portal pricing "
                "(partition_nous_models_by_tier), not this manifest."
            ),
        ) | {"display_name": "Nous Portal"},
        "models": _model_entries("nous"),
    }

    return {
        "version": CATALOG_VERSION,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": {
            "source": "hermes-agent repo",
            "docs": "https://hermes-agent.nousresearch.com/docs/reference/model-catalog",
        },
        "providers": providers,
    }


def main() -> int:
    catalog = build_catalog()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2)
        fh.write("\n")

    print(f"Wrote {OUTPUT_PATH}")
    for provider, block in catalog["providers"].items():
        print(f"  {provider}: {len(block['models'])} models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
