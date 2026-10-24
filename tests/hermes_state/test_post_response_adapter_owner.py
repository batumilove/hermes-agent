"""Durable delivery retains the receiving bot, independently of runtime profile."""

import pytest

from hermes_state import SessionDB


def test_barrier_receipt_retains_adapter_owner_and_rejects_cross_owner_retry(tmp_path):
    kwargs = dict(
        obligation_id="receipt",
        session_key="agent:satellite:telegram:dm:1",
        platform="telegram",
        chat_id="1",
        thread_id=None,
        content="final",
        actions=[],
        adapter_profile="receiving-bot",
    )
    with SessionDB(tmp_path / "state.db") as db:
        db.create_final_delivery_and_barrier(**kwargs)
        db.create_final_delivery_and_barrier(**kwargs)
    with SessionDB(tmp_path / "state.db") as db:
        with db._read_ctx() as conn:
            row = conn.execute(
                "SELECT adapter_profile FROM delivery_obligations WHERE obligation_id=?",
                ("receipt",),
            ).fetchone()
        assert row["adapter_profile"] == "receiving-bot"
        kwargs["adapter_profile"] = "satellite"
        with pytest.raises(
            ValueError, match="delivery obligation does not match retry"
        ):
            db.create_final_delivery_and_barrier(**kwargs)


def test_existing_receipts_survive_adapter_owner_column_reconciliation(tmp_path):
    path = tmp_path / "state.db"
    kwargs = dict(
        obligation_id="legacy-receipt",
        session_key="agent:default:telegram:dm:1",
        platform="telegram",
        chat_id="1",
        thread_id=None,
        content="legacy final",
        actions=[],
    )
    with SessionDB(path) as db:
        db.create_final_delivery_and_barrier(**kwargs)
        db._execute_write(lambda conn: conn.execute(
            "ALTER TABLE delivery_obligations DROP COLUMN adapter_profile"
        ))

    # The schema_version remains current: healing must use the live table shape.
    with SessionDB(path) as db:
        with db._read_ctx() as conn:
            legacy = conn.execute(
                "SELECT content, adapter_profile FROM delivery_obligations WHERE obligation_id=?",
                ("legacy-receipt",),
            ).fetchone()
        assert tuple(legacy) == ("legacy final", None)
        db.create_final_delivery_and_barrier(**kwargs)
        kwargs.update(obligation_id="new-receipt", adapter_profile="receiving-bot")
        db.create_final_delivery_and_barrier(**kwargs)
        with db._read_ctx() as conn:
            new = conn.execute(
                "SELECT adapter_profile FROM delivery_obligations WHERE obligation_id=?",
                ("new-receipt",),
            ).fetchone()
        assert new[0] == "receiving-bot"
