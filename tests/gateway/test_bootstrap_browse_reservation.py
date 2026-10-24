"""Gateway startup retries when browse maintenance releases between ownership probes."""

import pytest

from gateway.run_bootstrap import _reserve_waiting_out_browse_reconcile
from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership
from hermes_state_reconcile import BROWSE_RECONCILE_PURPOSE


@pytest.mark.platforms("posix")
def test_maintenance_release_after_reservation_conflict_allows_startup(tmp_path, monkeypatch):
    holder = ProfileOwnership()
    booting = ProfileOwnership()
    holder.reserve([tmp_path], purpose=BROWSE_RECONCILE_PURPOSE)
    reserve = booting.reserve
    released = False

    def release_after_conflict(homes):
        nonlocal released
        try:
            return reserve(homes)
        except OwnershipConflict:
            holder.close()
            released = True
            raise

    monkeypatch.setattr(booting, "reserve", release_after_conflict)
    try:
        _reserve_waiting_out_browse_reconcile(booting, [tmp_path])
        assert released
        assert booting.owns(tmp_path)
    finally:
        booting.close()
        holder.close()
