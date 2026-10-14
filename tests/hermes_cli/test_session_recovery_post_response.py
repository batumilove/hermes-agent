"""Offline recovery preserves receipt-linked work and its ordered claim ownership."""

import hashlib
import sqlite3

import pytest

from hermes_cli.session_lost_and_found import _copy_direct_tables
from hermes_cli.session_recovery import recover_session_database
from hermes_state import SessionDB


@pytest.mark.parametrize("mode", ["copy", "lost_and_found"])
def test_recovery_preserves_ordered_owed_actions_and_receiving_bot(tmp_path, mode):
    source = tmp_path / "source.db"
    output = tmp_path / "recovered.db"
    with SessionDB(source) as db:
        barrier = db.create_final_delivery_and_barrier(
            obligation_id="receipt", session_key="session", session_lineage="lineage",
            platform="telegram", chat_id="chat", thread_id=None, content="final",
            adapter_profile="receiving-bot",
            actions=[{"action_kind": "after_response", "payload": {"step": n}} for n in (1, 2)],
        )
        assert db.mark_final_delivery_attempting("receipt")
        assert db.settle_final_delivery("receipt", delivered=True)
    original = hashlib.sha256(source.read_bytes()).digest()

    if mode == "copy":
        report = recover_session_database(source, output, work_dir=tmp_path)
        assert report["copy"]["gateway_post_response_actions"]["copied_rows"] == 2
        assert report["verified"] and report["complete"]
    else:
        with SessionDB(output):
            pass
        reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        writer = sqlite3.connect(output, isolation_level=None)
        try:
            copied = _copy_direct_tables(reader, writer)
            assert copied["gateway_post_response_actions"] == 2
        finally:
            reader.close()
            writer.close()

    with SessionDB(output) as recovered:
        assert recovered.has_post_response_barrier("session", "lineage")
        receipt = recovered._read_one(
            "SELECT adapter_profile FROM delivery_obligations WHERE obligation_id='receipt'"
        )
        assert receipt["adapter_profile"] == "receiving-bot"
        for ordinal, key in enumerate(barrier.action_keys):
            claimed = recovered.claim_ready_post_response_actions()
            assert len(claimed) == 1
            action = claimed[0]
            assert (action.ordinal, action.action_key, action.payload) == (ordinal, key, {"step": ordinal + 1})
            assert recovered.complete_post_response_action(key, claimed_attempt=action.claim_attempt)
            assert not recovered.complete_post_response_action(key, claimed_attempt=action.claim_attempt)
        assert not recovered.has_post_response_barrier("session", "lineage")
    assert hashlib.sha256(source.read_bytes()).digest() == original
