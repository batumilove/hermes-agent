"""Daemon admission honors the reversible fork maintenance gate."""
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, admit_session_input, begin_runtime_epoch, claim_session_input


def test_external_drain_fences_transaction_and_preserves_queued_input(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s", source="cli")
        epoch = begin_runtime_epoch(db, instance_id="owner")
        runner = SimpleNamespace(_draining=False, _external_drain_active=False)
        authority = SessionAuthority(runner, profile_id="p", instance_id="owner", db=db, epoch=epoch)
        row = admit_session_input(db, epoch=epoch, principal_id="human", session_id="s",
                                  request_id="one", payload={})
        guard = authority._admission_gate()
        runner._external_drain_active = True
        with pytest.raises(RuntimeStoreError, match="runtime_draining"):
            authority._require_admission_open()
        with pytest.raises(RuntimeStoreError, match="runtime_draining"):
            claim_session_input(db, epoch=epoch, session_id="s", _guard=guard)
        runner._external_drain_active = False
        authority._require_admission_open()
        claimed = claim_session_input(db, epoch=epoch, session_id="s", _guard=guard)
        assert claimed["admission_id"] == row["admission_id"]
        runner._draining = True
        with pytest.raises(RuntimeStoreError, match="runtime_draining"):
            authority._require_admission_open()
    finally:
        db.close()


def test_release_wakes_existing_queues_but_never_reopens_shutdown(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        runner = SimpleNamespace(_draining=False, _external_drain_active=True)
        authority = SessionAuthority(runner, profile_id="p", instance_id="owner", db=db,
                                     epoch=begin_runtime_epoch(db, instance_id="owner"))
        authority.sessions["s"] = object()
        scheduled = []
        authority._schedule = scheduled.append
        authority.resume_after_external_drain()
        assert scheduled == []
        runner._external_drain_active = False
        authority.resume_after_external_drain()
        assert [ref.session_id for ref in scheduled] == ["s"]
        runner._draining = True
        authority.resume_after_external_drain()
        assert len(scheduled) == 1
    finally:
        db.close()
