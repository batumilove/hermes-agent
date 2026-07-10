from __future__ import annotations

import sys
import types


def test_build_from_sessions_db_opens_read_only_session_db(monkeypatch):
    import gateway.channel_directory as channel_directory

    captured = {}

    class FakeDB:
        def __init__(self, *args, **kwargs):
            captured["read_only"] = kwargs.get("read_only")
            captured["args"] = args

        def list_gateway_sessions(self, *, platform, active_only):
            captured["platform"] = platform
            captured["active_only"] = active_only
            return [
                {
                    "chat_id": "407304892",
                    "thread_id": "132092",
                    "display_name": "Aleman",
                    "chat_type": "dm",
                    "origin_json": None,
                }
            ]

        def close(self):
            captured["closed"] = True

    fake_hermes_state = types.SimpleNamespace(SessionDB=FakeDB)
    monkeypatch.setitem(sys.modules, "hermes_state", fake_hermes_state)

    rows = channel_directory._build_from_sessions_db("telegram")

    assert captured == {
        "read_only": True,
        "args": (),
        "platform": "telegram",
        "active_only": False,
        "closed": True,
    }
    assert rows == [
        {
            "id": "407304892:132092",
            "name": "Aleman / topic 132092",
            "type": "dm",
            "thread_id": "132092",
        }
    ]
