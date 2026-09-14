"""acquire_lease must re-select after a deferred single-use-token refresh.

Post-merge gate-sweep finding on the #71775 salvage (deferred refresh moved
OUTSIDE the pool lock). ``select()`` re-selects once the refreshed entries
are back in rotation (credential_pool.py, select() -> "if pending_refresh:
re-select"); ``acquire_lease()`` did not, so a pool whose only entries all
needed a refresh returned None even though the refresh had just succeeded —
the caller saw "no credentials available" and failed a request that should
have gone through.

These tests stub ``_available_entries`` / ``_refresh_pending_entries`` at the
same seam the production deferred-refresh contract uses: _available_entries
returns ``(available, pending_refresh)`` and entries pending a refresh are
NOT in ``available`` until the refresh has run.
"""

import threading
from dataclasses import replace

from agent.credential_pool import (
    STATUS_DEAD,
    CredentialPool,
    PooledCredential,
)


def _entry(entry_id: str) -> PooledCredential:
    return PooledCredential(
        id=entry_id,
        provider="anthropic",
        auth_type="oauth",
        access_token="tok",
        label=entry_id,
        source="oauth",
        priority=0,
    )


def _bare_pool(entries):
    """Minimal pool shell — avoids disk/keyring I/O in __init__."""
    pool = CredentialPool.__new__(CredentialPool)
    pool._lock = threading.RLock()
    pool._entries = list(entries)
    pool._active_leases = {}
    pool._current_id = None
    pool._max_concurrent = 2
    pool._unmatched_rotation_streak = 0
    pool.provider = "anthropic"
    return pool


def _wire_deferred_refresh(pool, *, refresh_succeeds: bool = True):
    """Model the deferred-refresh contract with an explicit state flag."""
    state = {"needs_refresh": True, "refresh_calls": 0}

    def fake_refresh(pending):
        state["refresh_calls"] += 1
        if refresh_succeeds:
            state["needs_refresh"] = False

    def fake_available(clear_expired=False, refresh=False):
        if state["needs_refresh"]:
            # Pending a refresh -> not yet available.
            pending = [(e.id, "tok") for e in pool._entries] if refresh else []
            return [], pending
        return list(pool._entries), []

    pool._refresh_pending_entries = fake_refresh
    pool._available_entries = fake_available
    return state


def test_acquire_lease_reselects_after_deferred_refresh():
    """The only entry needs a refresh; once refreshed it is available, so a
    lease MUST be granted rather than reporting no credentials."""
    pool = _bare_pool([_entry("e1")])
    state = _wire_deferred_refresh(pool)

    lease = pool.acquire_lease()

    assert state["refresh_calls"] == 1, "the deferred refresh should run once"
    assert state["needs_refresh"] is False, "entry is available post-refresh"
    assert lease == "e1", (
        "acquire_lease returned None despite a successfully refreshed, "
        "available entry — the caller would fail an answerable request"
    )
    assert pool._active_leases.get("e1") == 1, "the lease must be recorded"


def test_acquire_lease_without_pending_refresh_does_not_double_select():
    """No pending refresh -> exactly one selection pass (no wasted work)."""
    pool = _bare_pool([_entry("e1")])
    state = _wire_deferred_refresh(pool)
    state["needs_refresh"] = False  # already healthy

    passes = {"n": 0}
    original = pool._acquire_lease_under_lock

    def counting(credential_id):
        passes["n"] += 1
        return original(credential_id)

    pool._acquire_lease_under_lock = counting

    lease = pool.acquire_lease()

    assert lease == "e1"
    assert passes["n"] == 1, "healthy pool must not trigger the retry path"
    assert state["refresh_calls"] == 0


def test_acquire_lease_still_none_when_refresh_does_not_help():
    """If the refresh leaves nothing available, None is still the answer —
    the retry must not loop or invent a credential."""
    pool = _bare_pool([_entry("e1")])
    state = _wire_deferred_refresh(pool, refresh_succeeds=False)

    assert pool.acquire_lease() is None
    assert state["refresh_calls"] == 1, "retry must not refresh repeatedly"
    assert pool._active_leases == {}


def test_acquire_lease_rolls_back_partial_success_when_refresh_raises():
    """A sibling refresh failure cannot leak an already-recorded lease."""
    pool = _bare_pool([_entry("available"), _entry("pending")])
    pending_refresh = [("pending", "single-use-token")]
    pool._available_entries = lambda **_kwargs: ([pool._entries[0]], pending_refresh)

    def _raise_refresh(pending):
        del pending
        raise RuntimeError("refresh failed")

    pool._refresh_pending_entries = _raise_refresh

    import pytest

    with pytest.raises(RuntimeError, match="refresh failed"):
        pool.acquire_lease()

    assert pool._active_leases == {}


def test_acquire_lease_reselects_when_refresh_removes_chosen_entry():
    """A sibling refresh cannot leave a lease bound to a vanished entry."""
    available = _entry("available")
    refreshed_entry = _entry("pending")
    pool = _bare_pool([available, refreshed_entry])
    state = {"refreshed": False}

    def fake_available(**_kwargs):
        if not state["refreshed"]:
            return [available], [(refreshed_entry, "single-use-token")]
        return list(pool._entries), []

    def fake_refresh(pending):
        del pending
        state["refreshed"] = True
        pool._entries = [refreshed_entry]

    pool._available_entries = fake_available
    pool._refresh_pending_entries = fake_refresh

    assert pool.acquire_lease() == "pending"
    assert pool._active_leases == {"pending": 1}


def test_acquire_lease_rejects_unknown_explicit_credential_id():
    pool = _bare_pool([_entry("known")])

    assert pool.acquire_lease("missing") is None
    assert pool._active_leases == {}
    assert pool._current_id is None


def test_acquire_lease_rejects_dead_explicit_credential_id():
    dead = replace(_entry("dead"), last_status=STATUS_DEAD)
    pool = _bare_pool([dead, _entry("healthy")])

    assert pool.acquire_lease("dead") is None
    assert pool._active_leases == {}
    assert pool._current_id is None


def test_acquire_lease_revalidates_availability_after_sibling_refresh():
    chosen = _entry("chosen")
    sibling = _entry("sibling")
    pool = _bare_pool([chosen, sibling])
    refreshed = {"done": False}

    def fake_available(**_kwargs):
        available = [
            entry for entry in pool._entries if entry.last_status != STATUS_DEAD
        ]
        pending = [] if refreshed["done"] else [(sibling, "single-use-token")]
        return available, pending

    def fake_refresh(pending):
        assert pending
        refreshed["done"] = True
        pool._entries = [
            replace(entry, last_status=STATUS_DEAD)
            if entry.id == "chosen" else entry
            for entry in pool._entries
        ]

    pool._available_entries = fake_available
    pool._refresh_pending_entries = fake_refresh

    assert pool.acquire_lease("chosen") is None
    assert pool._active_leases == {}
    assert pool._current_id is None
