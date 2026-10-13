"""Behavioral contracts split from the main SessionDB test suite."""

import pytest
from hermes_state import SessionDB

@pytest.fixture
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


class TestGetMessagesAncestors:
    """get_messages(include_ancestors=True) spans the compression lineage (#51058)."""

    def test_include_ancestors_returns_lineage_messages(self, db):
        """After a compression rotation, include_ancestors=True returns the
        parent's rows plus the child continuation in insertion order."""
        db.create_session("parent", source="tui")
        db.append_message("parent", role="user", content="early ask")
        db.append_message("parent", role="assistant", content="early answer")
        # A real rotation stamps the parent end_reason='compression' (publish_compression_child).
        db.end_session("parent", "compression")

        db.create_session("child", source="tui", parent_session_id="parent")
        db.append_message("child", role="user", content="continuation ask")
        db.append_message("child", role="assistant", content="continuation answer")

        # Without the flag: only the child continuation (legacy behaviour).
        assert [m["content"] for m in db.get_messages("child")] == [
            "continuation ask",
            "continuation answer",
        ]
        # With the flag: the full root→tip transcript, insertion order.
        assert [m["content"] for m in db.get_messages("child", include_ancestors=True)] == [
            "early ask",
            "early answer",
            "continuation ask",
            "continuation answer",
        ]

    def test_include_ancestors_explicit_branch_stays_single_session(self, db):
        """Explicit /branch copies own their copied transcript: the lineage
        expansion must not pull the parent's rows in (parity with
        get_messages_as_conversation)."""
        db.create_session("parent", source="tui")
        db.append_message("parent", role="user", content="before branch")
        db.create_session(
            "branch",
            source="tui",
            parent_session_id="parent",
            model_config={"_branched_from": "parent"},
        )
        db.append_message("branch", role="user", content="copied turn")

        assert [m["content"] for m in db.get_messages("branch", include_ancestors=True)] == [
            "copied turn"
        ]

    def test_include_ancestors_api_fork_stays_single_session(self, db):
        """An API fork (parent ended 'branched', transcript copied, NO _branched_from
        marker — what _handle_fork_session produced before the marker fix) must not be
        mistaken for a compression lineage: ancestor expansion returns the fork's own
        copied transcript only, never the parent's rows re-prepended (which duplicated
        every turn and produced the [assistant, user, assistant] order ehz0ah hit)."""
        db.create_session("source", source="tui")
        db.append_message("source", role="user", content="question")
        db.append_message("source", role="assistant", content="answer")
        db.end_session("source", "branched")
        # Exactly what the API fork handler does, minus the _branched_from marker.
        db.create_session("fork", source="api_server", parent_session_id="source")
        db.replace_messages("fork", db.get_messages("source"))

        assert [m["content"] for m in db.get_messages("fork", include_ancestors=True)] == [
            "question",
            "answer",
        ]
        conversation = db.get_messages_as_conversation("fork", include_ancestors=True)
        assert [m["role"] for m in conversation] == ["user", "assistant"]
        assert [m["content"] for m in conversation] == ["question", "answer"]

    def test_include_ancestors_reset_child_stays_single_session(self, db):
        """A /reset continuation (_reset_from marker, parent ended 'session_reset') is a
        fresh conversation, not a compression lineage: ancestor expansion must not cross
        the reset boundary."""
        db.create_session("parent", source="tui")
        db.append_message("parent", role="user", content="old conversation")
        db.end_session("parent", "session_reset")
        db.create_session(
            "child",
            source="tui",
            parent_session_id="parent",
            model_config={"_reset_from": "parent"},
        )
        db.append_message("child", role="user", content="fresh start")

        assert [m["content"] for m in db.get_messages("child", include_ancestors=True)] == [
            "fresh start"
        ]
        conversation = db.get_messages_as_conversation("child", include_ancestors=True)
        assert [m["content"] for m in conversation] == ["fresh start"]

    def test_include_ancestors_deep_lineage_verified_chain(self, db):
        """A multi-hop compression lineage merges root→tip, and a plain (non-compression)
        grandparent above the root is not crossed: only verified compression hops count."""
        db.create_session("grandparent", source="tui")
        db.append_message("grandparent", role="user", content="plain old turn")
        db.end_session("grandparent", "stopped")
        db.create_session("root", source="tui", parent_session_id="grandparent")
        db.append_message("root", role="user", content="root turn")
        db.end_session("root", "compression")
        db.create_session("mid", source="tui", parent_session_id="root")
        db.append_message("mid", role="user", content="mid turn")
        db.end_session("mid", "compression")
        db.create_session("tip", source="tui", parent_session_id="mid")
        db.append_message("tip", role="user", content="tip turn")

        assert db._resume_lineage_ids("tip") == ["root", "mid", "tip"]
        assert [m["content"] for m in db.get_messages("tip", include_ancestors=True)] == [
            "root turn",
            "mid turn",
            "tip turn",
        ]
        conversation = db.get_messages_as_conversation("tip", include_ancestors=True)
        assert [m["content"] for m in conversation] == ["root turn", "mid turn", "tip turn"]

    def test_include_ancestors_paging_still_applies(self, db):
        """Paging applies to the merged lineage set, not per-session."""
        db.create_session("parent", source="tui")
        db.append_message("parent", role="user", content="p1")
        # A real rotation stamps the parent end_reason='compression' (publish_compression_child).
        db.end_session("parent", "compression")
        db.create_session("child", source="tui", parent_session_id="parent")
        for i in range(2, 6):
            db.append_message("child", role="user", content=f"c{i}")

        rows = db.get_messages("child", include_ancestors=True, limit=3, offset=0)
        assert [m["content"] for m in rows] == ["p1", "c2", "c3"]
        rows = db.get_messages("child", include_ancestors=True, limit=3, offset=3)
        assert [m["content"] for m in rows] == ["c4", "c5"]


class TestGetMessagesAncestorsGuards:
    """Paging guards for the merged-lineage read (get_messages)."""

    def test_after_id_rejected_with_include_ancestors(self, db):
        """after_id keyset paging must not silently drop the cursor on a merged
        lineage read (the multi-segment set is deduped before paging, so the
        per-row cursor is not meaningful across segments)."""
        db.create_session("parent", source="tui")
        db.append_message("parent", role="user", content="p1")
        db.end_session("parent", "compression")
        db.create_session("child", source="tui", parent_session_id="parent")
        db.append_message("child", role="user", content="c1")
        anchor = db.get_messages("child")[0]

        with pytest.raises(ValueError, match="after_id is incompatible with include_ancestors"):
            db.get_messages("child", include_ancestors=True, after_id=anchor["id"])


class TestHandoffTimestampCarry:
    """#59661: carried handoff rows keep the durable parent row's timestamp so
    ``_display_dedupe_key`` collapses each logical message once in the lineage read."""

    @staticmethod
    def _rotate(db, parent_id, child_id, handoff):
        db.publish_compression_child(
            parent_session_id=parent_id, child_session_id=child_id, source="test",
            messages=handoff, require_compression_lease=False)

    def test_rotation_handoff_tail_appears_once_across_the_lineage(self, db):
        """The probe shape: parent tail rows re-inserted as child rows without a timestamp must
        not duplicate in the display lineage read."""
        db.create_session("root", source="test")
        for role, text in (("user", "u0"), ("assistant", "a0"), ("user", "u4"), ("assistant", "a4"),
                           ("user", "u5"), ("assistant", "a5")):
            db.append_message("root", role=role, content=text)
        handoff = [{"role": "assistant", "content": "SUMMARY"},
                   {"role": "user", "content": "u4"},
                   {"role": "assistant", "content": "a4"},
                   {"role": "user", "content": "u5"},
                   {"role": "assistant", "content": "a5"}]

        self._rotate(db, "root", "child", handoff)

        lineage = db.get_messages("child", include_ancestors=True)
        contents = [(m["role"], m["content"]) for m in lineage]
        assert len(contents) == len(set(contents)) == 7  # each logical message exactly once
        assert [m["content"] for m in lineage] == ["u0", "a0", "u4", "a4", "u5", "a5", "SUMMARY"]
        # The carry is observable on the caller's dicts: carried rows hold the parent's durable ts.
        parent_ts = {m["content"]: m["timestamp"] for m in db.get_messages("root")}
        for carried in handoff[1:]:
            assert carried["timestamp"] == parent_ts[carried["content"]]

    def test_handoff_dedupe_across_three_rotation_boundaries(self, db):
        """The same tail dicts carried root -> seg1 -> seg2 -> seg3 (reused across publishes, as
        production does) collapse to exactly one copy of each logical message in seg3's lineage."""
        db.create_session("root", source="test")
        for role, text in (("user", "u0"), ("assistant", "a0"),
                           ("user", "tail-u"), ("assistant", "tail-a")):
            db.append_message("root", role=role, content=text)
        tail = [{"role": "user", "content": "tail-u"}, {"role": "assistant", "content": "tail-a"}]
        for child_id, parent_id in (("seg1", "root"), ("seg2", "seg1"), ("seg3", "seg2")):
            handoff = [{"role": "assistant", "content": f"SUMMARY-{child_id}"}, *tail]
            self._rotate(db, parent_id, child_id, handoff)

        lineage = db.get_messages("seg3", include_ancestors=True)
        contents = [(m["role"], m["content"]) for m in lineage]
        assert len(contents) == len(set(contents)) == 7
        assert [m["content"] for m in lineage] == [
            "u0", "a0", "tail-u", "tail-a", "SUMMARY-seg1", "SUMMARY-seg2", "SUMMARY-seg3"]

    def test_distinct_same_content_turns_are_not_collapsed(self, db):
        """Repeated identical turns are DISTINCT logical messages: the identity collapse must never
        merge two turns that merely share content."""
        db.create_session("s", source="test")
        db.append_messages_batch("s", [{"role": "user", "content": "ok"}, {"role": "user", "content": "ok"}])
        # One batch: the second row's timestamp is bumped past the first (:540 monotonic bump).
        assert [m["content"] for m in db.get_messages("s", include_compacted=True)] == ["ok", "ok"]

        # Cross-boundary: a carried "ok" and a fresh post-rotation "ok" both display.
        db.create_session("root", source="test")
        db.append_message("root", role="user", content="ok")
        self._rotate(db, "root", "seg", [
            {"role": "assistant", "content": "SUMMARY"},
            {"role": "user", "content": "ok"},  # carried: adopts the root row's timestamp
        ])
        db.append_message("seg", role="user", content="ok")  # fresh turn: its own newer timestamp
        oks = [m for m in db.get_messages("seg", include_ancestors=True) if m["content"] == "ok"]
        assert len(oks) == 2

    def test_carry_parent_timestamps_does_not_overwrite_carried_timestamp(self, db):
        """Handoff dicts that already carry a timestamp are never clobbered by the carry."""
        db.create_session("root", source="test")
        db.append_message("root", role="user", content="u1")
        db.append_message("root", role="assistant", content="a1")
        stored = {m["content"]: m["timestamp"] for m in db.get_messages("root")}
        handoff = [{"role": "assistant", "content": "SUMMARY"},
                   {"role": "user", "content": "u1", "timestamp": stored["u1"]},
                   {"role": "assistant", "content": "a1", "timestamp": stored["a1"] + 500.0},
                   {"role": "user", "content": "fresh", "timestamp": 1234.5}]

        self._rotate(db, "root", "child", handoff)

        assert handoff[1]["timestamp"] == stored["u1"]
        assert handoff[2]["timestamp"] == stored["a1"] + 500.0
        assert handoff[3]["timestamp"] == 1234.5
        contents = [(m["role"], m["content"])
                    for m in db.get_messages("child", include_ancestors=True)]
        # u1 collapses (carried the durable ts); the offset a1 does not (carried ts is authoritative).
        assert contents.count(("user", "u1")) == 1
        assert contents.count(("assistant", "a1")) == 2
        assert contents.count(("user", "fresh")) == 1

    def test_archive_and_compact_carried_rows_keep_identity(self, db):
        """In-place double compaction: a row carried through both compactions keeps a stable display
        identity (same durable timestamp) and never duplicates in the display read."""
        db.create_session("s", source="test")
        for role, text in (("user", "u0"), ("assistant", "a0"),
                           ("user", "keep me"), ("assistant", "tail-a")):
            db.append_message("s", role=role, content=text)
        carried = [{"role": "user", "content": "keep me"}, {"role": "assistant", "content": "tail-a"}]
        db.archive_and_compact("s", [{"role": "assistant", "content": "S1"}, *carried], tail_count=2)
        first_ts = {m["content"]: m["timestamp"] for m in db.get_messages("s")}

        db.append_messages_batch(
            "s", [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}])
        db.archive_and_compact(
            "s",
            [{"role": "assistant", "content": "S2"}, *carried,
             {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}],
            tail_count=4)

        visible = db.get_messages("s", include_compacted=True)
        contents = [(m["role"], m["content"]) for m in visible]
        assert len(contents) == len(set(contents))
        assert [m["content"] for m in visible] == ["u0", "a0", "S1", "S2", "keep me", "tail-a", "u2", "a2"]
        second_ts = {m["content"]: m["timestamp"] for m in db.get_messages("s")}
        assert second_ts["keep me"] == first_ts["keep me"]
        assert second_ts["tail-a"] == first_ts["tail-a"]
