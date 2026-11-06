import contextvars
import gc
import threading
import weakref
import pytest
from agent.periodic_scheduler import PeriodicScheduler

class Owner:
    pass

@pytest.fixture(autouse=True)
def no_cyclic_gc():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()

def owned_handle(body, interval):
    owner = Owner()
    value = contextvars.ContextVar('owner')
    token = value.set(owner)
    try:
        handle = PeriodicScheduler().schedule(lambda owner=owner: body(), interval)
    finally:
        value.reset(token)
    owner.handle = handle
    return owner, handle, weakref.ref(owner)

def test_idle_cancel_releases_owner_without_gc():
    owner, handle, ref = owned_handle(lambda: None, 3600)
    assert handle.cancel(wait=1) is True
    del owner
    assert ref() is None
    assert handle._fn is None and handle._context is None

def test_false_self_stop_releases_owner_without_gc():
    owner, handle, ref = owned_handle(lambda: False, .001)
    del owner
    with handle._scheduler._cond:
        assert handle._scheduler._cond.wait_for(lambda: handle.cancelled and handle._runner is None, 2)
    assert ref() is None
    assert handle._fn is None and handle._context is None

@pytest.mark.parametrize('raises', [False, True])
def test_cancel_timeout_retains_until_body_ceases(raises):
    entered, release = threading.Event(), threading.Event()
    def body():
        entered.set()
        assert release.wait(2)
        if raises:
            raise RuntimeError('injected callback exception')
    owner, handle, ref = owned_handle(body, .001)
    assert entered.wait(2)
    del owner
    try:
        assert handle.cancel(wait=.001) is False
        assert ref() is not None
        assert handle._fn is not None and handle._context is not None
    finally:
        release.set()
        assert handle.cancel(wait=2) is True
    assert ref() is None
    assert handle._fn is None and handle._context is None
