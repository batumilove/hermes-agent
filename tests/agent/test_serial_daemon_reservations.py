"""Abandoned daemon jobs retain their own capacity reservation until completion."""

import asyncio
import threading

import pytest

from agent import async_utils
from tests.waiting import async_wait_until


def test_late_worker_releases_its_own_reservation(monkeypatch):
    class Reservation:
        def __init__(self):
            self.slots = threading.BoundedSemaphore(1)
            self.released = threading.Event()

        def acquire(self, *, blocking=False):
            return self.slots.acquire(blocking=blocking)

        def release(self):
            self.slots.release()
            self.released.set()

    old_reservation = Reservation()
    new_reservation = Reservation()
    old_started, new_started = threading.Event(), threading.Event()
    release_old, release_new = threading.Event(), threading.Event()

    def old_work():
        old_started.set()
        release_old.wait()
        return "old"

    def new_work():
        new_started.set()
        release_new.wait()
        return "new"

    async def exercise():
        new = None
        try:
            monkeypatch.setattr(async_utils, "_serial_job_slots", old_reservation)
            monkeypatch.setattr(async_utils, "_SERIAL_JOB_TIMEOUT_SECONDS", 0.05)
            old = asyncio.create_task(async_utils.run_sync_in_detached_serial_daemon_thread(old_work))
            assert await async_wait_until(old_started.is_set, timeout=2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(old, timeout=2)

            monkeypatch.setattr(async_utils, "_serial_job_slots", new_reservation)
            monkeypatch.setattr(async_utils, "_SERIAL_JOB_TIMEOUT_SECONDS", 5)
            new = asyncio.create_task(async_utils.run_sync_in_detached_serial_daemon_thread(new_work))
            assert await async_wait_until(new_started.is_set, timeout=2)
            release_old.set()
            assert await async_wait_until(
                lambda: old_reservation.released.is_set() or new_reservation.released.is_set(),
                timeout=2,
            )
            assert old_reservation.released.is_set()
            assert not new_reservation.released.is_set()
            assert old_reservation.acquire()
            assert not new_reservation.acquire()
            release_new.set()
            assert await asyncio.wait_for(new, timeout=2) == "new"
        finally:
            release_old.set()
            release_new.set()
            if new is not None:
                await asyncio.gather(new, return_exceptions=True)

    asyncio.run(exercise())
