"""Model-facing credential guidance agrees across the input and vault paths."""

import json
from unittest.mock import patch

from model_tools import _apply_dynamic_schemas
from tools.browser_vault_tool import BROWSER_VAULT_SCHEMA, browser_vault_list


def test_input_and_vault_guidance_allow_user_authorized_secrets():
    definitions = _apply_dynamic_schemas([
        {"type": "function", "function": {
            "name": "browser_exec", "description": "Browser input.", "parameters": {},
        }},
        {"type": "function", "function": BROWSER_VAULT_SCHEMA},
    ])
    descriptions = [definition["function"]["description"] for definition in definitions]
    descriptions.append(BROWSER_VAULT_SCHEMA["description"])
    for description in descriptions:
        assert "Never type a password, card or code a page shows" in description
        assert "user gives or has you fetch may be typed" in description
        assert "don't repeat it" in description
        assert "recommending the vault" in description
    vault = BROWSER_VAULT_SCHEMA["description"]
    assert "explicit user authorization" in vault
    assert "verifying the intended tab and origin" in vault
    assert "does not provide the vault's secret-isolation guarantee" in vault
    assert "enter_code does not accept literal codes or email connector handles" in vault
    assert "card fields after the user confirms" in vault
    assert "origin matches the item" in vault
    assert "Stop on save_declined; never retry payment_declined" in vault


def test_empty_vault_hint_offers_authorized_input_and_save_fallback():
    with patch("agent.vault_backends.enabled_backends", return_value=[]):
        result = json.loads(browser_vault_list())
    assert result["success"] is True
    assert result["items"] == []
    hint = result["hint"]
    assert "user gave you the password or authorized you to fetch it" in hint
    assert "never echo it" in hint
    assert "end your reply" in hint and "save it to the vault" in hint
    assert "Otherwise call action=save_login" in hint
    assert "Never type a password shown on the page" in hint
