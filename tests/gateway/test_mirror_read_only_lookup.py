from __future__ import annotations

import sys
import types


def test_find_session_id_uses_read_only_db_for_lookup(monkeypatch):
    import gateway.mirror as mirror

    captured = {}

    class FakeDB:
        def __init__(self, *args, **kwargs):
            captured["read_only"] = kwargs.get("read_only")
            captured["args"] = args

        def find_session_by_origin(self, **kwargs):
            captured["lookup"] = kwargs
            return "session-123"

        def close(self):
            captured["closed"] = True

    monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(SessionDB=FakeDB))

    assert mirror._find_session_id(
        "telegram",
        "407304892",
        thread_id="132092",
        user_id="407304892",
    ) == "session-123"
    assert captured == {
        "read_only": True,
        "args": (),
        "lookup": {
            "platform": "telegram",
            "chat_id": "407304892",
            "thread_id": "132092",
            "user_id": "407304892",
        },
        "closed": True,
    }
