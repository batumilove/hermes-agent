"""Credential lease revalidation around deferred refresh."""
from __future__ import annotations
from typing import Optional, Tuple, List
from agent import credential_pool as _source

def acquire_lease(self, credential_id: Optional[str] = None) -> Optional[str]:
    """Acquire a soft lease on a credential.

    If a specific credential_id is provided, lease that entry directly.
    Otherwise prefer the least-leased available credential, using priority as
    a stable tie-breaker. When every credential is already at the soft cap,
    still return the least-leased one instead of blocking.
    """
    chosen_id, pending_refresh = self._acquire_lease_under_lock(credential_id)
    if pending_refresh:
        try:
            self._refresh_pending_entries(pending_refresh)
        except BaseException:
            # Acquisition may already have incremented an available entry
            # before returning refresh work for its siblings.
            if chosen_id is not None:
                self.release_lease(chosen_id)
            raise
        # Revalidate the already-recorded lease after refresh.  Deferred
        # refresh runs outside the pool lock and may quarantine/remove an
        # entry while merging refreshed state.  Never return a lease for
        # an entry that no longer exists or became unavailable; release it
        # and use the same one-shot re-selection path below.
        with self._lock:
            available_now, _ = self._available_entries(
                clear_expired=False, refresh=False
            )
            if chosen_id is not None and not any(
                entry.id == chosen_id for entry in available_now
            ):
                self.release_lease(chosen_id)
                if self._current_id == chosen_id:
                    self._current_id = None
                chosen_id = None

            # Mirror select(): if nothing was leasable, or refresh removed
            # the initially leased entry, retry now that refreshed entries
            # are back in rotation.  The retry deliberately ignores any
            # new pending work so acquisition remains bounded to one
            # deferred-refresh pass.
            if chosen_id is None:
                chosen_id, _ = self._acquire_lease_under_lock(credential_id)
    return chosen_id

def _acquire_lease_under_lock(
    self, credential_id: Optional[str],
    ) -> tuple[Optional[str], list[tuple]]:
    """Run lease acquisition under the lock, returning id + pending refreshes."""
    with self._lock:
        available, pending_refresh = self._available_entries(
            clear_expired=True, refresh=True
        )
        if credential_id:
            if not any(entry.id == credential_id for entry in available):
                return None, pending_refresh
            self._active_leases[credential_id] = self._active_leases.get(credential_id, 0) + 1
            self._current_id = credential_id
            return credential_id, pending_refresh

        if not available:
            return None, pending_refresh

        below_cap = [
            entry for entry in available
            if self._active_leases.get(entry.id, 0) < self._max_concurrent
        ]
        candidates = below_cap if below_cap else available
        chosen = min(
            candidates,
            key=lambda entry: (self._active_leases.get(entry.id, 0), entry.priority),
        )
        self._active_leases[chosen.id] = self._active_leases.get(chosen.id, 0) + 1
        self._current_id = chosen.id
        return chosen.id, pending_refresh
