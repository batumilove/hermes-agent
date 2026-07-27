"""Tests for Telegram DM Private Chat Topics (Bot API 9.4).

Covers:
- _setup_dm_topics: loading persisted thread_ids from config
- _setup_dm_topics: creating new topics via API when no thread_id
- _persist_dm_topic_thread_id: saving thread_id back to config.yaml
- _get_dm_topic_info: looking up topic config by thread_id
- _cache_dm_topic_from_message: caching thread_ids from incoming messages
- _build_message_event: DM topic resolution in message events
"""

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)

    # Register telegram.constants as a separate module mock so that
    # ``from telegram.constants import ChatType`` resolves to our mock
    # with string-valued members (not auto-generated MagicMocks).
    constants_mod = MagicMock()
    constants_mod.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    constants_mod.ChatType.GROUP = "group"
    constants_mod.ChatType.SUPERGROUP = "supergroup"
    constants_mod.ChatType.CHANNEL = "channel"
    constants_mod.ChatType.PRIVATE = "private"

    sys.modules["telegram"] = telegram_mod
    sys.modules["telegram.ext"] = telegram_mod.ext
    sys.modules["telegram.constants"] = constants_mod
    sys.modules["telegram.request"] = telegram_mod.request

    # Force reimport so the adapter picks up the mock ChatType.
    sys.modules.pop("plugins.platforms.telegram.adapter", None)


_ensure_telegram_mock()

from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


def _make_adapter(dm_topics_config=None, group_topics_config=None):
    """Create a TelegramAdapter with optional DM/group topics config."""
    extra = {}
    if dm_topics_config is not None:
        extra["dm_topics"] = dm_topics_config
    if group_topics_config is not None:
        extra["group_topics"] = group_topics_config
    config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter = TelegramAdapter(config)
    return adapter


# ── _setup_dm_topics: load persisted thread_ids ──


@pytest.mark.asyncio
async def test_setup_dm_topics_loads_persisted_thread_ids():
    """Topics with thread_id in config should be loaded into cache, not created."""
    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [
                {"name": "General", "thread_id": 100},
                {"name": "Work", "thread_id": 200},
            ],
        }
    ])
    adapter._bot = AsyncMock()

    await adapter._setup_dm_topics()

    # Both should be in cache
    assert adapter._dm_topics["111:General"] == 100
    assert adapter._dm_topics["111:Work"] == 200
    # create_forum_topic should NOT have been called
    adapter._bot.create_forum_topic.assert_not_called()


@pytest.mark.asyncio
async def test_setup_dm_topics_creates_when_no_thread_id():
    """Topics without thread_id should be created via API."""
    adapter = _make_adapter([
        {
            "chat_id": 222,
            "topics": [
                {"name": "NewTopic", "icon_color": 7322096},
            ],
        }
    ])
    adapter._bot = AsyncMock()
    mock_topic = SimpleNamespace(message_thread_id=999)
    adapter._bot.create_forum_topic.return_value = mock_topic

    # Mock the persist method so it doesn't touch the filesystem
    adapter._persist_dm_topic_thread_id = MagicMock()

    await adapter._setup_dm_topics()

    # Should have been created
    adapter._bot.create_forum_topic.assert_called_once_with(
        chat_id=222, name="NewTopic", icon_color=7322096,
    )
    # Should be in cache
    assert adapter._dm_topics["222:NewTopic"] == 999
    # Should persist
    adapter._persist_dm_topic_thread_id.assert_called_once_with(222, "NewTopic", 999)


@pytest.mark.asyncio
async def test_setup_dm_topics_mixed_persisted_and_new():
    """Mix of persisted and new topics should work correctly."""
    adapter = _make_adapter([
        {
            "chat_id": 333,
            "topics": [
                {"name": "Existing", "thread_id": 50},
                {"name": "New", "icon_color": 123},
            ],
        }
    ])
    adapter._bot = AsyncMock()
    mock_topic = SimpleNamespace(message_thread_id=777)
    adapter._bot.create_forum_topic.return_value = mock_topic
    adapter._persist_dm_topic_thread_id = MagicMock()

    await adapter._setup_dm_topics()

    # Existing loaded from config
    assert adapter._dm_topics["333:Existing"] == 50
    # New created via API
    assert adapter._dm_topics["333:New"] == 777
    # Only one API call (for "New")
    adapter._bot.create_forum_topic.assert_called_once()


@pytest.mark.asyncio
async def test_setup_dm_topics_skips_empty_config():
    """Empty dm_topics config should be a no-op."""
    adapter = _make_adapter([])
    adapter._bot = AsyncMock()

    await adapter._setup_dm_topics()

    adapter._bot.create_forum_topic.assert_not_called()
    assert adapter._dm_topics == {}


@pytest.mark.asyncio
async def test_setup_dm_topics_no_config():
    """No dm_topics in config at all should be a no-op."""
    adapter = _make_adapter()
    adapter._bot = AsyncMock()

    await adapter._setup_dm_topics()

    adapter._bot.create_forum_topic.assert_not_called()


# ── _create_dm_topic: error handling ──


@pytest.mark.asyncio
async def test_create_dm_topic_handles_duplicate_error():
    """Duplicate topic error should return None gracefully."""
    adapter = _make_adapter()
    adapter._bot = AsyncMock()
    adapter._bot.create_forum_topic.side_effect = Exception("topic_name_duplicate")

    result = await adapter._create_dm_topic(chat_id=111, name="General")

    assert result is None


@pytest.mark.asyncio
async def test_create_dm_topic_handles_generic_error():
    """Generic error should return None with warning."""
    adapter = _make_adapter()
    adapter._bot = AsyncMock()
    adapter._bot.create_forum_topic.side_effect = Exception("some random error")

    result = await adapter._create_dm_topic(chat_id=111, name="General")

    assert result is None


@pytest.mark.asyncio
async def test_create_dm_topic_returns_none_without_bot():
    """No bot instance should return None."""
    adapter = _make_adapter()
    adapter._bot = None

    result = await adapter._create_dm_topic(chat_id=111, name="General")

    assert result is None


@pytest.mark.asyncio
async def test_ensure_dm_topic_creates_on_demand_and_persists():
    """Named delivery targets should create missing private DM topics on demand."""
    adapter = _make_adapter()
    adapter._bot = AsyncMock()
    adapter._bot.create_forum_topic.return_value = SimpleNamespace(message_thread_id=444)
    adapter._persist_dm_topic_thread_id = MagicMock()

    result = await adapter.ensure_dm_topic("111", "On Demand")

    assert result == "444"
    adapter._bot.create_forum_topic.assert_called_once_with(
        chat_id=111,
        name="On Demand",
    )
    assert adapter._dm_topics["111:On Demand"] == 444
    assert adapter._dm_topics_config == [
        {"chat_id": 111, "topics": [{"name": "On Demand", "thread_id": 444}]}
    ]
    adapter._persist_dm_topic_thread_id.assert_called_once_with(
        111, "On Demand", 444, replace_existing=False
    )


@pytest.mark.asyncio
async def test_ensure_dm_topic_force_create_replaces_persisted_thread_id():
    """Refreshing a stale named topic should replace the cached persisted thread_id."""
    adapter = _make_adapter()
    bot = AsyncMock()
    bot.create_forum_topic.return_value = SimpleNamespace(message_thread_id=777)
    adapter._bot = bot
    adapter._persist_dm_topic_thread_id = MagicMock()
    adapter._dm_topics = {"111:General": 500}
    adapter._dm_topics_config = [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 500}]}
    ]

    result = await adapter.ensure_dm_topic("111", "General", force_create=True)

    assert result == "777"
    bot.create_forum_topic.assert_called_once_with(chat_id=111, name="General")
    assert adapter._dm_topics["111:General"] == 777
    assert adapter._dm_topics_config[0]["topics"][0]["thread_id"] == 777
    adapter._persist_dm_topic_thread_id.assert_called_once_with(
        111, "General", 777, replace_existing=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("force_create", [False, True])
async def test_ensure_dm_topic_persist_false_creates_without_mutating_local_or_file_state(
    tmp_path,
    monkeypatch,
    force_create,
):
    """Ephemeral generated topics must never become durable named-topic mappings."""
    import yaml

    topic_name = "Cron: Report · job1 · 07-23 06:00:00 UTC"
    original_topics = [
        {
            "chat_id": 111,
            "topics": [
                {"name": topic_name, "thread_id": 500},
                {"name": "Manual", "thread_id": 600},
            ],
        }
    ]
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "platforms": {
                    "telegram": {"extra": {"dm_topics": original_topics}}
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    original_file = config_file.read_bytes()

    adapter = _make_adapter(
        [
            {
                "chat_id": 111,
                "topics": [
                    {"name": topic_name, "thread_id": 500},
                    {"name": "Manual", "thread_id": 600},
                ],
            }
        ]
    )
    adapter._dm_topics = {
        f"111:{topic_name}": 500,
        "111:Manual": 600,
    }
    original_cache = dict(adapter._dm_topics)
    original_config = yaml.safe_load(yaml.safe_dump(adapter._dm_topics_config))
    adapter._bot = AsyncMock()
    adapter._bot.create_forum_topic.return_value = SimpleNamespace(message_thread_id=777)
    adapter._persist_dm_topic_thread_id = MagicMock()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = await adapter.ensure_dm_topic(
        "111",
        topic_name,
        force_create=force_create,
        persist=False,
    )

    assert result == "777"
    adapter._bot.create_forum_topic.assert_awaited_once_with(
        chat_id=111,
        name=topic_name,
    )
    assert adapter._dm_topics == original_cache
    assert adapter._dm_topics_config == original_config
    adapter._persist_dm_topic_thread_id.assert_not_called()
    assert config_file.read_bytes() == original_file


@pytest.mark.asyncio
async def test_ensure_dm_topic_persist_false_failed_creation_leaves_state_unchanged(
    tmp_path,
    monkeypatch,
):
    """Failed ephemeral creation must not mutate cache, config, or config.yaml."""
    import yaml

    topic_name = "Cron: Failed · job1 · 07-23 06:01:00 UTC"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "platforms": {
                    "telegram": {
                        "extra": {
                            "dm_topics": [
                                {
                                    "chat_id": 111,
                                    "topics": [
                                        {"name": "Manual", "thread_id": 600}
                                    ],
                                }
                            ]
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    original_file = config_file.read_bytes()
    adapter = _make_adapter(
        [{"chat_id": 111, "topics": [{"name": "Manual", "thread_id": 600}]}]
    )
    adapter._dm_topics = {"111:Manual": 600}
    adapter._bot = AsyncMock()
    adapter._bot.create_forum_topic.side_effect = RuntimeError("creation failed")
    adapter._persist_dm_topic_thread_id = MagicMock()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = await adapter.ensure_dm_topic("111", topic_name, persist=False)

    assert result is None
    assert adapter._dm_topics == {"111:Manual": 600}
    assert adapter._dm_topics_config == [
        {"chat_id": 111, "topics": [{"name": "Manual", "thread_id": 600}]}
    ]
    adapter._persist_dm_topic_thread_id.assert_not_called()
    assert config_file.read_bytes() == original_file


# ── _persist_dm_topic_thread_id ──


def test_persist_dm_topic_thread_id_writes_config(tmp_path):
    """Should write thread_id into the correct topic in config.yaml."""
    import yaml

    config_data = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": [
                        {
                            "chat_id": 111,
                            "topics": [
                                {"name": "General", "icon_color": 123},
                                {"name": "Work", "icon_color": 456},
                            ],
                        }
                    ]
                }
            }
        }
    }

    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    adapter = _make_adapter()

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        adapter._persist_dm_topic_thread_id(111, "General", 999)

    with open(config_file) as f:
        result = yaml.safe_load(f)

    topics = result["platforms"]["telegram"]["extra"]["dm_topics"][0]["topics"]
    assert topics[0]["thread_id"] == 999
    assert "thread_id" not in topics[1]  # "Work" should be untouched


def test_persist_dm_topic_thread_id_skips_if_already_set(tmp_path):
    """Should not overwrite an existing thread_id."""
    import yaml

    config_data = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": [
                        {
                            "chat_id": 111,
                            "topics": [
                                {"name": "General", "icon_color": 123, "thread_id": 500},
                            ],
                        }
                    ]
                }
            }
        }
    }

    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    adapter = _make_adapter()

    with patch.object(Path, "home", return_value=tmp_path):
        adapter._persist_dm_topic_thread_id(111, "General", 999)

    with open(config_file) as f:
        result = yaml.safe_load(f)

    topics = result["platforms"]["telegram"]["extra"]["dm_topics"][0]["topics"]
    assert topics[0]["thread_id"] == 500  # unchanged


def test_persist_dm_topic_thread_id_replaces_existing_when_requested(tmp_path):
    """Forced refresh should overwrite a stale persisted thread_id."""
    import yaml

    config_data = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": [
                        {
                            "chat_id": 111,
                            "topics": [
                                {"name": "General", "icon_color": 123, "thread_id": 500},
                            ],
                        }
                    ]
                }
            }
        }
    }

    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    adapter = _make_adapter()

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        adapter._persist_dm_topic_thread_id(111, "General", 999, replace_existing=True)

    with open(config_file) as f:
        result = yaml.safe_load(f)

    topics = result["platforms"]["telegram"]["extra"]["dm_topics"][0]["topics"]
    assert topics[0]["thread_id"] == 999


# ── _get_dm_topic_info ──


def test_persist_dm_topic_thread_id_preserves_config_on_write_failure(tmp_path):
    """Failed writes should leave the original config.yaml intact."""
    import yaml

    config_data = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": [
                        {
                            "chat_id": 111,
                            "topics": [
                                {"name": "General", "icon_color": 123},
                            ],
                        }
                    ]
                }
            }
        }
    }

    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    original_text = yaml.dump(config_data)
    config_file.write_text(original_text, encoding="utf-8")

    adapter = _make_adapter()

    def fail_dump(*args, **kwargs):
        raise RuntimeError("boom")

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}), \
         patch("yaml.dump", side_effect=fail_dump):
        adapter._persist_dm_topic_thread_id(111, "General", 999)

    assert config_file.read_text(encoding="utf-8") == original_text
    result = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    topics = result["platforms"]["telegram"]["extra"]["dm_topics"][0]["topics"]
    assert "thread_id" not in topics[0]


def test_get_dm_topic_info_finds_cached_topic():
    """Should return topic config when thread_id is in cache."""
    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [
                {"name": "General", "skill": "my-skill"},
            ],
        }
    ])
    adapter._dm_topics["111:General"] = 100

    result = adapter._get_dm_topic_info("111", "100")

    assert result is not None
    assert result["name"] == "General"
    assert result["skill"] == "my-skill"


def test_get_dm_topic_info_returns_none_for_unknown():
    """Should return None for unknown thread_id."""
    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [{"name": "General"}],
        }
    ])
    # Mock reload to avoid filesystem access
    adapter._reload_dm_topics_from_config = lambda: None

    result = adapter._get_dm_topic_info("111", "999")

    assert result is None


def test_get_dm_topic_info_returns_none_without_config():
    """Should return None if no dm_topics config."""
    adapter = _make_adapter()
    adapter._reload_dm_topics_from_config = lambda: None

    result = adapter._get_dm_topic_info("111", "100")

    assert result is None


def test_get_dm_topic_info_returns_none_for_none_thread():
    """Should return None if thread_id is None."""
    adapter = _make_adapter([
        {"chat_id": 111, "topics": [{"name": "General"}]}
    ])

    result = adapter._get_dm_topic_info("111", None)

    assert result is None


def test_get_dm_topic_info_hot_reloads_from_config(tmp_path):
    """Should find a topic added to config after startup (hot-reload)."""
    import yaml

    # Start with empty topics
    adapter = _make_adapter([
        {"chat_id": 111, "topics": []}
    ])

    # Write config with a new topic + thread_id
    config_data = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": [
                        {
                            "chat_id": 111,
                            "topics": [
                                {"name": "NewProject", "thread_id": 555},
                            ],
                        }
                    ]
                }
            }
        }
    }
    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        result = adapter._get_dm_topic_info("111", "555")

    assert result is not None
    assert result["name"] == "NewProject"
    # Should now be cached
    assert adapter._dm_topics["111:NewProject"] == 555


# ── _cache_dm_topic_from_message ──


def test_cache_dm_topic_from_message():
    """Should cache a new topic mapping."""
    adapter = _make_adapter()

    adapter._cache_dm_topic_from_message("111", "100", "General")

    assert adapter._dm_topics["111:General"] == 100


def test_cache_dm_topic_from_message_no_overwrite():
    """Should not overwrite an existing cached topic."""
    adapter = _make_adapter()
    adapter._dm_topics["111:General"] = 100

    adapter._cache_dm_topic_from_message("111", "999", "General")

    assert adapter._dm_topics["111:General"] == 100  # unchanged


# ── _build_message_event: auto_skill binding ──


def _make_mock_message(chat_id=111, chat_type="private", text="hello", thread_id=None,
                       user_id=42, user_name="Test User", forum_topic_created=None,
                       is_topic_message=None, is_forum=None):
    """Create a mock Telegram Message for _build_message_event tests."""
    chat = SimpleNamespace(
        id=chat_id,
        type=chat_type,
        title=None,
    )
    if is_forum is not None:
        chat.is_forum = is_forum
    # Add full_name attribute for DM chats
    if not hasattr(chat, "full_name"):
        chat.full_name = user_name

    user = SimpleNamespace(
        id=user_id,
        full_name=user_name,
    )

    if is_topic_message is None:
        is_topic_message = bool(thread_id) if chat_type == "private" else None

    msg = SimpleNamespace(
        chat=chat,
        from_user=user,
        text=text,
        message_thread_id=thread_id,
        is_topic_message=is_topic_message,
        message_id=1001,
        reply_to_message=None,
        date=None,
        forum_topic_created=forum_topic_created,
    )
    return msg


def test_build_message_event_sets_auto_skill():
    """When topic has a skill binding, auto_skill should be set on the event."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [
                {"name": "My Project", "skill": "accessibility-auditor", "thread_id": 100},
            ],
        }
    ])
    adapter._dm_topics["111:My Project"] = 100

    msg = _make_mock_message(chat_id=111, thread_id=100, text="check this page")
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill == "accessibility-auditor"
    # chat_topic should be the clean topic name, no [skill: ...] suffix
    assert event.source.chat_topic == "My Project"


def test_build_message_event_no_auto_skill_without_binding():
    """Topics without skill binding should have auto_skill=None."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [
                {"name": "General", "thread_id": 200},
            ],
        }
    ])
    adapter._dm_topics["111:General"] = 200

    msg = _make_mock_message(chat_id=111, thread_id=200)
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic == "General"


def test_build_message_event_no_auto_skill_without_thread():
    """Regular DM messages (no thread_id) should have auto_skill=None."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    msg = _make_mock_message(chat_id=111, thread_id=None)
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None


def test_build_message_event_filters_non_topic_dm_thread_id():
    """A DM reply-thread id should not be persisted unless Telegram marks it as a topic message."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    msg = _make_mock_message(chat_id=111, thread_id=777, is_topic_message=False)
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.source.thread_id is None
    assert event.source.chat_topic is None
    assert event.auto_skill is None


def test_build_message_event_preserves_true_dm_topic_thread_id():
    """True DM topic messages should keep their thread id for routing."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter([
        {
            "chat_id": 111,
            "topics": [
                {"name": "General", "thread_id": 200},
            ],
        }
    ])
    adapter._dm_topics["111:General"] = 200

    msg = _make_mock_message(chat_id=111, thread_id=200, is_topic_message=True)
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.source.thread_id == "200"
    assert event.source.chat_topic == "General"


# ── _build_message_event: group_topics skill binding ──

# The telegram mock sets sys.modules["telegram.constants"] = telegram_mod (root mock),
# so `from telegram.constants import ChatType` in telegram.py resolves to
# telegram_mod.ChatType — not telegram_mod.constants.ChatType.  We must use
# the same ChatType object the production code sees so equality checks work.
from telegram.constants import ChatType as _ChatType  # noqa: E402


def test_group_topic_skill_binding():
    """Group topic with skill config should set auto_skill on the event."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {
            "chat_id": -1001234567890,
            "topics": [
                {"name": "Engineering", "thread_id": 5, "skill": "software-development"},
                {"name": "Sales", "thread_id": 12, "skill": "sales-framework"},
            ],
        }
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=5,
        text="hello",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill == "software-development"
    assert event.source.chat_topic == "Engineering"


def test_group_topic_skill_binding_second_topic():
    """A different thread_id in the same group should resolve its own skill."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {
            "chat_id": -1001234567890,
            "topics": [
                {"name": "Engineering", "thread_id": 5, "skill": "software-development"},
                {"name": "Sales", "thread_id": 12, "skill": "sales-framework"},
            ],
        }
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=12,
        text="deal update",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill == "sales-framework"
    assert event.source.chat_topic == "Sales"


def test_group_topic_no_skill_binding():
    """Group topic without a skill key should have auto_skill=None but set chat_topic."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {
            "chat_id": -1001234567890,
            "topics": [
                {"name": "General", "thread_id": 1},
            ],
        }
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=1,
        text="hey",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic == "General"


def test_group_topic_unmapped_thread_id():
    """Thread ID not in config should fall through — no skill, no topic name."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {
            "chat_id": -1001234567890,
            "topics": [
                {"name": "Engineering", "thread_id": 5, "skill": "software-development"},
            ],
        }
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=999,
        text="random",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic is None


def test_group_topic_unmapped_chat_id():
    """Chat ID not in group_topics config should fall through silently."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {
            "chat_id": -1001234567890,
            "topics": [
                {"name": "Engineering", "thread_id": 5, "skill": "software-development"},
            ],
        }
    ])

    msg = _make_mock_message(
        chat_id=-1009999999999,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=5,
        text="wrong group",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic is None


def test_group_topic_no_config():
    """No group_topics config at all should be fine — no skill, no topic."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()  # no group_topics_config

    msg = _make_mock_message(
        chat_id=-1001234567890, chat_type=_ChatType.GROUP, thread_id=5, text="hi"
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic is None


def test_group_topic_chat_id_int_string_coercion():
    """chat_id as string in config should match integer chat.id via str() coercion."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {
            "chat_id": "-1001234567890",  # string, not int
            "topics": [
                {"name": "Dev", "thread_id": "7", "skill": "hermes-agent-dev"},
            ],
        }
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=7,
        text="test",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill == "hermes-agent-dev"
    assert event.source.chat_topic == "Dev"


def test_group_topic_mapping_shape_config():
    """Operator-edited mapping shape {chat_id: [topics]} must resolve like the list shape."""
    from gateway.platforms.base import MessageType

    # Dict/mapping shape instead of the canonical list-of-entries shape.
    adapter = _make_adapter(group_topics_config={
        "-1001234567890": [
            {"name": "Engineering", "thread_id": 5, "skill": "software-development"},
            {"name": "Sales", "thread_id": 12, "skill": "sales-framework"},
        ],
    })

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=12,
        text="deal update",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill == "sales-framework"
    assert event.source.chat_topic == "Sales"


def test_group_topic_malformed_config_does_not_crash():
    """Non-dict entries / non-list topics must be skipped, not raise AttributeError."""
    from gateway.platforms.base import MessageType

    # Junk list entries (str) are filtered out; a matching entry with a good
    # topic still resolves; non-dict topic entries within it are skipped.
    adapter = _make_adapter(group_topics_config=[
        "not-a-dict",
        {"chat_id": -1001234567890, "topics": ["also-not-a-dict",
                                               {"name": "Good", "thread_id": 5}]},
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=5,
        text="hi",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic == "Good"


def test_group_topic_non_list_topics_does_not_crash():
    """A matched entry whose topics is not a list must fall through, not raise."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=[
        {"chat_id": -1001234567890, "topics": "oops-not-a-list"},
    ])

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=5,
        text="hi",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic is None


def test_group_topic_scalar_config_falls_through():
    """A scalar (int/str) group_topics value must fall through cleanly, not raise."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter(group_topics_config=42)

    msg = _make_mock_message(
        chat_id=-1001234567890,
        chat_type=_ChatType.SUPERGROUP,
        thread_id=5,
        text="hi",
        is_topic_message=True,
        is_forum=True,
    )
    event = adapter._build_message_event(msg, MessageType.TEXT)

    assert event.auto_skill is None
    assert event.source.chat_topic is None


# ── _build_message_event: from_user=None fallback in DMs ──


def test_build_message_event_dm_from_user_none_falls_back_to_chat_id():
    """When from_user is None in a DM, user_id should fall back to chat.id."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    msg = _make_mock_message(chat_id=12345, user_id=42, user_name="Alice")
    # Simulate from_user being None (edge case on fresh restart / forwarded msg)
    msg.from_user = None

    event = adapter._build_message_event(msg, MessageType.TEXT)

    # Should fall back to chat.id since chat_type is "dm"
    assert event.source.user_id == "12345"
    assert event.source.user_name == "Alice"  # falls back to chat.full_name


def test_build_message_event_group_from_user_none_stays_none():
    """When from_user is None in a group, user_id should remain None."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    msg = _make_mock_message(
        chat_id=-1001234567890, chat_type=_ChatType.SUPERGROUP,
        user_id=42, user_name="Alice"
    )
    msg.from_user = None

    event = adapter._build_message_event(msg, MessageType.TEXT)

    # Groups should NOT fall back — anonymous senders stay None
    assert event.source.user_id is None
    assert event.source.user_name is None


def test_build_message_event_dm_from_user_present_uses_user():
    """When from_user is present in a DM, it should be used (no fallback)."""
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    msg = _make_mock_message(chat_id=12345, user_id=99999, user_name="Bob")

    event = adapter._build_message_event(msg, MessageType.TEXT)

    # Normal case — from_user is used directly
    assert event.source.user_id == "99999"
    assert event.source.user_name == "Bob"


# ── _reload_dm_topics_from_config: mtime/size guard ──


def _write_config(tmp_path, dm_topics_list):
    """Write a config.yaml with the given dm_topics list and return the path."""
    import yaml

    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_data = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": dm_topics_list,
                }
            }
        }
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file


def _hermes_home_ctx(tmp_path):
    """Context manager pair for HERMES_HOME pointing at tmp_path/.hermes."""
    return patch.object(Path, "home", return_value=tmp_path), \
        patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")})


def test_reload_uses_fast_yaml_loader(tmp_path):
    adapter = _make_adapter([])
    _write_config(
        tmp_path,
        [{"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}],
    )
    parsed = {
        "platforms": {
            "telegram": {
                "extra": {
                    "dm_topics": [
                        {
                            "chat_id": 111,
                            "topics": [{"name": "General", "thread_id": 100}],
                        }
                    ]
                }
            }
        }
    }

    with (
        patch.object(Path, "home", return_value=tmp_path),
        patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}),
        patch("utils.fast_safe_load", return_value=parsed) as fast_load,
        patch("yaml.safe_load", side_effect=AssertionError("slow YAML loader used")),
    ):
        adapter._reload_dm_topics_from_config()

    fast_load.assert_called_once()
    assert adapter._dm_topics["111:General"] == 100


def test_reload_skips_reparse_when_config_unchanged(tmp_path):
    """_reload_dm_topics_from_config should not re-parse when mtime+size are unchanged."""
    import yaml

    adapter = _make_adapter([])
    _write_config(tmp_path, [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        # First reload: parses and caches
        adapter._reload_dm_topics_from_config()
        assert adapter._dm_topics.get("111:General") == 100

        # Patch yaml.safe_load to detect re-parse
        original_safe_load = yaml.safe_load
        call_count = {"n": 0}

        def counting_safe_load(stream):
            call_count["n"] += 1
            return original_safe_load(stream)

        with patch("yaml.safe_load", side_effect=counting_safe_load):
            adapter._reload_dm_topics_from_config()

        # Should NOT have re-parsed because config hasn't changed
        assert call_count["n"] == 0
        # Data is still correct
        assert adapter._dm_topics.get("111:General") == 100


def test_reload_reparses_when_mtime_changes(tmp_path):
    """_reload_dm_topics_from_config should re-parse when config mtime changes."""
    import time

    adapter = _make_adapter([])
    _write_config(tmp_path, [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        adapter._reload_dm_topics_from_config()
        assert adapter._dm_topics.get("111:General") == 100

        # Modify config: new topic added
        _write_config(tmp_path, [
            {"chat_id": 111, "topics": [
                {"name": "General", "thread_id": 100},
                {"name": "Work", "thread_id": 200},
            ]}
        ])
        # Ensure mtime is different even on fast filesystems
        cf = tmp_path / ".hermes" / "config.yaml"
        st = cf.stat()
        os.utime(cf, (st.st_atime, st.st_mtime + 2))

        adapter._reload_dm_topics_from_config()
        # New topic should now be loaded
        assert adapter._dm_topics.get("111:Work") == 200


def test_reload_reparses_when_size_changes_same_mtime(tmp_path):
    """Even if mtime is unchanged, a different file size should trigger re-parse."""
    adapter = _make_adapter([])
    _write_config(tmp_path, [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        adapter._reload_dm_topics_from_config()

        # Write a different-size config but preserve mtime
        cf = tmp_path / ".hermes" / "config.yaml"
        old_mtime = cf.stat().st_mtime

        _write_config(tmp_path, [
            {"chat_id": 111, "topics": [
                {"name": "General", "thread_id": 100},
                {"name": "Work", "thread_id": 200},
            ]}
        ])
        # Force same mtime
        os.utime(cf, (old_mtime, old_mtime))

        adapter._reload_dm_topics_from_config()
        assert adapter._dm_topics.get("111:Work") == 200


def test_reload_reparses_same_inode_size_and_mtime_when_ctime_changes(tmp_path):
    """An in-place same-size rewrite must not evade the unchanged-file guard."""
    adapter = _make_adapter([])
    config_file = _write_config(tmp_path, [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        adapter._reload_dm_topics_from_config()
        original = config_file.stat()

        _write_config(tmp_path, [
            {"chat_id": 111, "topics": [{"name": "Special", "thread_id": 100}]}
        ])
        rewritten = config_file.stat()
        assert rewritten.st_ino == original.st_ino
        assert rewritten.st_size == original.st_size
        os.utime(config_file, ns=(original.st_atime_ns, original.st_mtime_ns))

        adapter._reload_dm_topics_from_config()

    assert adapter._dm_topics.get("111:Special") == 100


def test_reload_handles_missing_config(tmp_path):
    """_reload_dm_topics_from_config should be a no-op when config.yaml is missing."""
    adapter = _make_adapter([
        {"chat_id": 111, "topics": [{"name": "General"}]}
    ])

    # No config.yaml in tmp_path
    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        # Should not raise
        adapter._reload_dm_topics_from_config()
        # Existing config should be preserved (not cleared)
        assert len(adapter._dm_topics_config) == 1


def test_reload_recovers_after_parse_failure(tmp_path):
    """After a parse failure, the next call with valid config should succeed."""
    import yaml

    adapter = _make_adapter([])
    config_file = tmp_path / ".hermes" / "config.yaml"
    config_file.parent.mkdir(parents=True)

    # Write invalid YAML
    config_file.write_text("{{{{invalid yaml")

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        # First call: parse fails, should not crash
        adapter._reload_dm_topics_from_config()
        # Should NOT have cached anything from the failed parse
        assert "111:General" not in adapter._dm_topics

        # Now write valid config with different mtime
        _write_config(tmp_path, [
            {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
        ])
        cf = tmp_path / ".hermes" / "config.yaml"
        st = cf.stat()
        os.utime(cf, (st.st_atime, st.st_mtime + 2))

        # Second call: should succeed
        adapter._reload_dm_topics_from_config()
        assert adapter._dm_topics.get("111:General") == 100


def test_reload_clears_topics_when_config_emptied(tmp_path):
    """When config goes from having topics to having none, cache should reflect that."""
    adapter = _make_adapter([])
    _write_config(tmp_path, [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        adapter._reload_dm_topics_from_config()
        assert len(adapter._dm_topics_config) == 1

        # Write config with empty dm_topics
        _write_config(tmp_path, [])
        cf = tmp_path / ".hermes" / "config.yaml"
        st = cf.stat()
        os.utime(cf, (st.st_atime, st.st_mtime + 2))

        adapter._reload_dm_topics_from_config()
        assert adapter._dm_topics_config == []
        assert adapter._dm_topic_chat_ids == set()


@pytest.mark.asyncio
async def test_get_dm_topic_info_schedules_reload_off_event_loop(monkeypatch):
    adapter = _make_adapter([])
    event_loop_thread = threading.get_ident()
    read_threads = []
    original_topics = adapter._dm_topics

    def read_config_snapshot():
        read_threads.append(threading.get_ident())
        return (
            (1, 2, 3, 4, 5),
            [
                {
                    "chat_id": 111,
                    "topics": [{"name": "General", "thread_id": 999}],
                }
            ],
        )

    monkeypatch.setattr(
        adapter, "_read_dm_topics_config_snapshot", read_config_snapshot
    )

    assert adapter._get_dm_topic_info("111", "999") is None
    task = adapter._dm_topics_config_reload_task
    assert isinstance(task, asyncio.Task)
    await task
    await asyncio.sleep(0)
    assert adapter._dm_topics_config_reload_task is None
    assert read_threads and read_threads[0] != event_loop_thread
    assert adapter._dm_topics is not original_topics
    assert original_topics == {}
    assert adapter._get_dm_topic_info("111", "999") == {
        "name": "General",
        "thread_id": 999,
    }


@pytest.mark.asyncio
async def test_dm_topic_reload_task_consumes_failure_and_clears_handle(
    monkeypatch, caplog
):
    adapter = _make_adapter([])
    caplog.set_level("DEBUG")
    monkeypatch.setattr(
        adapter,
        "_refresh_dm_topics_config_async",
        AsyncMock(side_effect=RuntimeError("malformed dm_topics snapshot")),
    )

    adapter._schedule_dm_topics_config_reload()
    task = adapter._dm_topics_config_reload_task
    assert isinstance(task, asyncio.Task)
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert task.done()
    assert adapter._dm_topics_config_reload_task is None
    assert "DM-topic config reload failed" in caplog.text


@pytest.mark.asyncio
async def test_pre_message_refresh_routes_first_message_after_external_update(
    monkeypatch,
):
    adapter = _make_adapter([])
    event_loop_thread = threading.get_ident()
    read_threads = []

    def read_config_snapshot():
        read_threads.append(threading.get_ident())
        return (
            (1, 2, 3, 4, 5),
            [
                {
                    "chat_id": 111,
                    "topics": [
                        {
                            "name": "NewProject",
                            "thread_id": 555,
                            "skill": "project-skill",
                        }
                    ],
                }
            ],
        )

    monkeypatch.setattr(
        adapter, "_read_dm_topics_config_snapshot", read_config_snapshot
    )

    await adapter._refresh_dm_topics_before_update(None, None)

    assert read_threads and read_threads[0] != event_loop_thread
    assert adapter._get_dm_topic_info("111", "555") == {
        "name": "NewProject",
        "thread_id": 555,
        "skill": "project-skill",
    }


@pytest.mark.asyncio
async def test_concurrent_dm_topic_refreshes_serialize_worker_reads(monkeypatch):
    adapter = _make_adapter([])
    state_lock = threading.Lock()
    active_reads = 0
    max_active_reads = 0

    def read_config_snapshot():
        nonlocal active_reads, max_active_reads
        with state_lock:
            active_reads += 1
            max_active_reads = max(max_active_reads, active_reads)
        time.sleep(0.05)
        with state_lock:
            active_reads -= 1
        return None

    monkeypatch.setattr(
        adapter, "_read_dm_topics_config_snapshot", read_config_snapshot
    )

    await asyncio.gather(
        adapter._refresh_dm_topics_config_async(),
        adapter._refresh_dm_topics_config_async(),
    )

    assert max_active_reads == 1


def test_get_dm_topic_info_no_excessive_reload_on_miss(tmp_path):
    """Multiple _get_dm_topic_info misses on unchanged config should parse only once."""
    adapter = _make_adapter([
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])
    _write_config(tmp_path, [
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 100}]}
    ])

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
        # First miss triggers a reload
        result1 = adapter._get_dm_topic_info("111", "999")
        assert result1 is None

        # Second miss should NOT trigger another reload (config unchanged)
        import yaml
        original_safe_load = yaml.safe_load
        call_count = {"n": 0}

        def counting_safe_load(stream):
            call_count["n"] += 1
            return original_safe_load(stream)

        with patch("yaml.safe_load", side_effect=counting_safe_load):
            result2 = adapter._get_dm_topic_info("111", "998")
            result3 = adapter._get_dm_topic_info("111", "997")

        assert result2 is None
        assert result3 is None
        assert call_count["n"] == 0
