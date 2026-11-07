"""Collector integration: bounded reader ownership, post-quiescence transfer, backend validation.

Every test binds the ORIGINAL frozen Receipt module by byte hash; no synthetic
receipt reimplementation is trusted here. The repository fixture is Linux-specific.
No network, credentials or activation.
"""
import asyncio
import contextvars
import gc
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import os
import secrets
import signal
import threading
import time
import weakref
from pathlib import Path

import pytest

from hermes_cli import plugins_turn_boundary as boundary
from hermes_cli import plugins_turn_evidence_collector as collector_mod
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tests.waiting import async_wait_until

ORIGINAL_RECEIPTS = Path(__file__).parent / "fixtures/turn_evidence/delivery_receipts.py.fixture"
ORIGINAL_RECEIPTS_SHA256 = "d41cb8b704f03e4f496e8a8816795a73bb0d7649fbbee722fe4ca28a4dab8f52"

_loader = SourceFileLoader("original_delivery_receipts", str(ORIGINAL_RECEIPTS))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
receipts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(receipts)

TID, TID2 = "a" * 32, "c" * 32
RID, CID = "b" * 16, "d" * 16
RID2, CID2 = "e" * 16, "f" * 16
TURN = "collector-turn"


@pytest.fixture
def world(tmp_path, monkeypatch):
    assert hashlib.sha256(ORIGINAL_RECEIPTS.read_bytes()).hexdigest() == ORIGINAL_RECEIPTS_SHA256
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o700)
    manager = PluginManager(scope_key=str(tmp_path))
    context = PluginContext(PluginManifest(name="collector-test"), manager)
    monkeypatch.setattr("hermes_cli.plugins._resolve_hook_callback_timeout", lambda: 0.02)
    monkeypatch.setattr("hermes_cli.plugins_dispatch._PLUGIN_COMMAND_AWAIT_TIMEOUT_SECS", 0.02)
    yield {"manager": manager, "context": context, "directory": directory,
           "key": secrets.token_bytes(32)}
    with boundary._CUSTODY_LOCK:
        boundary._RETAINED.clear()


def make_collector(world, **kwargs):
    return collector_mod.TurnEvidenceCollector(
        world["manager"], world["context"], receipts, turn_id=TURN,
        receipt_directory=str(world["directory"]), key=world["key"], **kwargs)


def completed_trace(c, trace_id=TID, root=RID, child=CID):
    receipt = c.open_trace(trace_id, root)
    receipt.started(child)
    receipt.ended(child)
    receipt.ended(root)
    assert receipt.finish("normal")
    assert c.retain(trace_id) is not None
    return receipt


def backend_for(trace_id=TID, root=RID, child=CID, complete=True):
    observations = [{"id": root, "trace_id": trace_id, "parent_id": None, "ended": True}]
    if child is not None:
        observations.append({"id": child, "trace_id": trace_id, "parent_id": root, "ended": True})
    return {"trace_id": trace_id, "complete": complete, "producer_terminal_verified": True,
            "local_receipt_current": True, "observations": observations}


def verified_payload(world, envelope, binding):
    return collector_mod.TransferVerifier(world["key"]).verify(
        envelope, expected_binding=binding)


@pytest.mark.platforms("linux")
def test_full_cycle_two_traces_collect_transfer_and_backend_validation(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
        completed_trace(c, TID2, RID2, CID2)
    result = c.collect()
    assert result[TID]["status"] == "collected" and result[TID]["proof"]["valid"] is True
    assert result[TID2]["status"] == "collected" and result[TID2]["proof"]["valid"] is True
    for trace_id in (TID, TID2):
        envelope = c.issue_transfer(trace_id)
        assert envelope["trace_id"] == trace_id and envelope["proof"]["valid"] is True
        binding = c.expected_binding(trace_id)
        assert binding["receipt_sha256"] and binding["registration_sha256"]
        payload = verified_payload(world, envelope, binding)
        assert payload is not None
        root_id, child_id = (RID, CID) if trace_id == TID else (RID2, CID2)
        assert collector_mod.validate_backend(
            payload, backend_for(trace_id, root_id, child_id),
            receipts=receipts) == "captured_inventory_delivered"


@pytest.mark.platforms("linux")
def test_collect_inside_open_turn_is_unknown_and_does_not_consume(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
        assert c.collect(deadline_seconds=0.05)[TID]["status"] == "unknown"
    assert c.collect()[TID]["status"] == "collected"


@pytest.mark.platforms("linux")
def test_host_helper_deadline_invalidates_permanently(world):
    c = make_collector(world)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()

    async def callback(**kw):
        entered.set()
        try:
            assert release.wait(5)
        finally:
            ended.set()

    world["context"].register_hook("on_session_finalize", callback)

    async def scenario():
        with c.begin_turn() as scope:
            completed_trace(c)
            world["manager"].invoke_hook("on_session_finalize", turn_id=TURN)
            assert entered.is_set() and scope.pending == 1
        assert c.collect(deadline_seconds=0.05)[TID]["status"] == "unverified"
        assert c.issue_transfer(TID) is None
        release.set()
        assert ended.wait(5)
        await async_wait_until(lambda: scope.pending == 0, timeout=5,
                               what="helper exit accounting")
        assert c.collect()[TID]["status"] == "unverified"

    asyncio.run(scenario())


@pytest.mark.platforms("linux")
def test_late_copied_context_dispatch_after_close_is_unverified(world):
    c = make_collector(world)
    calls = []
    world["context"].register_hook("pre_tool_call", lambda **kw: calls.append(kw))
    with c.begin_turn():
        completed_trace(c)
        copied = contextvars.copy_context()
    result = copied.run(world["manager"].invoke_hook, "pre_tool_call", turn_id=TURN)
    assert result[0]["action"] == "block" and calls == []
    assert c.collect()[TID]["status"] == "unverified"
    assert c.issue_transfer(TID) is None


@pytest.mark.platforms("linux")
def test_registration_generation_change_invalidates(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    world["context"].register_hook("post_llm_call", lambda **kw: None)
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_abandoned_context_blocks_transfer_permanently(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    assert c.collect()[TID]["status"] == "collected"
    world["context"]._load_abandoned = True
    assert c.issue_transfer(TID) is None
    world["context"]._load_abandoned = False
    assert c.issue_transfer(TID) is None  # latched; restoration cannot resurrect
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_capacity_eviction_returns_unknown(world):
    c = make_collector(world, max_traces=1)
    with c.begin_turn():
        completed_trace(c)
        completed_trace(c, TID2, RID2, CID2)
    result = c.collect()
    assert result[TID]["status"] == "unknown"
    assert result[TID2]["status"] == "collected"
    assert c.issue_transfer(TID) is None


@pytest.mark.platforms("linux")
def test_retention_expiry_returns_unknown(world):
    clock = {"now": 0.0}
    c = make_collector(world, retention_seconds=10.0, monotonic=lambda: clock["now"])
    with c.begin_turn():
        completed_trace(c)
    clock["now"] = 11.0
    assert c.collect()[TID]["status"] == "unknown"
    assert c.issue_transfer(TID) is None


@pytest.mark.platforms("linux")
def test_expiry_after_collection_drops_proof_before_transfer(world):
    clock = {"now": 0.0}
    c = make_collector(world, retention_seconds=10.0, monotonic=lambda: clock["now"])
    with c.begin_turn():
        completed_trace(c)
    assert c.collect()[TID]["status"] == "collected"
    clock["now"] = 11.0
    assert c.issue_transfer(TID) is None


@pytest.mark.platforms("linux")
def test_missing_receipt_snapshot_is_unknown(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    assert c.collect()[TID]["status"] == "collected"
    (world["directory"] / f"{TID}.json").unlink()
    assert c.issue_transfer(TID) is None
    assert c.collect()[TID]["status"] == "unknown"


@pytest.mark.platforms("linux")
def test_transfer_is_single_use_per_trace(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c.collect()
    assert c.issue_transfer(TID) is not None
    with pytest.raises(RuntimeError):
        c.issue_transfer(TID)
    assert c.collect()[TID]["status"] == "transferred"


@pytest.mark.platforms("linux")
def test_issue_requires_collected_proof(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    assert c.issue_transfer(TID) is None
    assert c.retain(TID) is None  # already retained; double retention refused


@pytest.mark.platforms("linux")
def test_verifier_rejects_forgeries_and_rebinding(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c.collect()
    envelope = c.issue_transfer(TID)
    binding = dict(c.binding(), trace_id=TID)
    ok = collector_mod.TransferVerifier(world["key"])
    assert ok.verify(envelope, expected_binding=binding) is not None
    wrong_key = collector_mod.TransferVerifier(secrets.token_bytes(32))
    assert wrong_key.verify(envelope, expected_binding=binding) is None
    for field, value in (("turn_sha256", "0" * 64), ("profile", "other-profile"),
                         ("trace_id", TID2), ("receipt_sha256", "1" * 64), ("nonce", "0" * 32)):
        forged = dict(envelope, **{field: value})
        assert ok.verify(forged, expected_binding=binding) is None, field
    tampered_proof = dict(envelope, proof=dict(envelope["proof"], valid=True))
    assert ok.verify(tampered_proof, expected_binding=binding) is None
    foreign_producer = dict(binding, producer={"pid": 1, "start_ticks": 1, "boot_id": "0" * 32})
    assert ok.verify(envelope, expected_binding=foreign_producer) is None


@pytest.mark.platforms("linux")
def test_verifier_freshness_and_clock_skew(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
        completed_trace(c, TID2, RID2, CID2)
    c.collect()
    stale = c.issue_transfer(TID, now=time.time() - 1000)
    fresh = c.issue_transfer(TID2)
    binding = dict(c.binding(), trace_id=TID)
    strict = collector_mod.TransferVerifier(world["key"], freshness_seconds=10.0)
    assert strict.verify(stale, expected_binding=binding) is None
    past_clock = collector_mod.TransferVerifier(world["key"], clock=lambda: time.time() - 1000)
    assert past_clock.verify(fresh, expected_binding=dict(c.binding(), trace_id=TID2)) is None


@pytest.mark.platforms("linux")
def test_backend_mismatch_and_incomplete_are_not_acceptance(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c.collect()
    payload = verified_payload(world, c.issue_transfer(TID), dict(c.binding(), trace_id=TID))
    assert payload is not None
    mismatched = backend_for()
    mismatched["observations"][1]["parent_id"] = None
    assert collector_mod.validate_backend(payload, mismatched, receipts=receipts) == "mismatch"
    incomplete = backend_for(complete=False)
    assert collector_mod.validate_backend(payload, incomplete, receipts=receipts) == "unverified"
    missing_child = backend_for(child=None)
    assert collector_mod.validate_backend(payload, missing_child, receipts=receipts) == "mismatch"


@pytest.mark.platforms("linux")
def test_stale_receipt_directory_blocks_reuse_as_unverified(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c.collect()
    c2 = make_collector(world)
    with c2.begin_turn():
        receipt = c2.open_trace(TID, RID)
        assert c2.retain(TID) is not None
        receipt.ended(RID)
        assert receipt.finish("normal") is False  # exclusive receipt file already exists
    result = c2.collect()
    assert result[TID]["status"] == "collected"
    assert result[TID]["proof"]["valid"] is False
    assert c2.issue_transfer(TID) is None  # fresh authority re-read refuses to sign
    assert c2.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_fork_child_cannot_collect_or_transfer(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGALRM, lambda *_: os._exit(99))
        signal.alarm(3)
        try:
            assert c.collect()[TID]["status"] == "unknown"
            assert c.issue_transfer(TID) is None
            assert c.open_trace("9" * 32, "8" * 16) is None
            assert c.retain(TID) is None
            os._exit(0)
        except BaseException:
            os._exit(98)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert c.collect()[TID]["status"] == "collected"


@pytest.mark.platforms("linux")
def test_async_outstanding_work_blocks_until_quiescence(world):
    c = make_collector(world)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def callback(**kw):
            entered.set()
            await release.wait()

        world["context"].register_hook("on_session_finalize", callback)
        with c.begin_turn() as scope:
            completed_trace(c)
            task = asyncio.create_task(
                world["manager"].ainvoke_hook("on_session_finalize", turn_id=TURN))
            await entered.wait()
            assert scope.pending > 0
            assert c.collect(deadline_seconds=0.05)[TID]["status"] == "unknown"
        release.set()
        await task
        await async_wait_until(lambda: scope.pending == 0, timeout=5,
                               what="child completion accounting")
        assert c.collect()[TID]["status"] == "collected"

    asyncio.run(scenario())


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("after_collect", ["registration", "late_dispatch", "abandonment", "late_started"])
def test_post_collection_invalidation_blocks_transfer(world, after_collect):
    c = make_collector(world)
    world["context"].register_hook("pre_tool_call", lambda **kw: None)
    with c.begin_turn():
        receipt = completed_trace(c)
        copied = contextvars.copy_context()
    assert c.collect()[TID]["status"] == "collected"
    if after_collect == "registration":
        world["context"].register_hook("post_llm_call", lambda **kw: None)
    elif after_collect == "late_dispatch":
        copied.run(world["manager"].invoke_hook, "pre_tool_call", turn_id=TURN)
    elif after_collect == "abandonment":
        world["context"]._load_abandoned = True
    else:
        receipt.started(CID)  # late observation on a sealed receipt invalidates it
    assert c.issue_transfer(TID) is None
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_caller_cannot_mutate_collected_authority(world):
    c = make_collector(world)
    with c.begin_turn():
        receipt = completed_trace(c)
        receipt.invalidate()
    result = c.collect()
    assert result[TID]["status"] == "collected" and result[TID]["proof"]["valid"] is False
    result[TID]["proof"]["valid"] = True
    result[TID]["proof"]["producer"] = {"pid": 1, "start_ticks": 1, "boot_id": "0" * 32}
    assert c.collect()[TID]["proof"]["valid"] is False  # returned copies never alias the authority
    assert c.issue_transfer(TID) is None
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_failed_retains_do_not_pin_receipts(world):
    c = make_collector(world)
    refs = []
    for i in range(40):
        trace_id = f"{i:032x}"
        refs.append(weakref.ref(c.open_trace(trace_id, RID)))
        assert c.retain(trace_id) is None  # no own boundary executing
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert all(r["status"] == "unverified" for r in c.collect().values())


@pytest.mark.platforms("linux")
def test_evicted_reader_releases_custody_at_next_collect(world):
    c = make_collector(world, max_traces=1)
    with c.begin_turn():
        ref_first = weakref.ref(completed_trace(c))
        completed_trace(c, TID2, RID2, CID2)  # evicts the first trace
    result = c.collect()
    assert result[TID]["status"] == "unknown"
    assert result[TID2]["status"] == "collected"
    gc.collect()
    assert ref_first() is None


@pytest.mark.platforms("linux")
def test_expired_reader_releases_custody_at_next_collect(world):
    clock = {"now": 0.0}
    c = make_collector(world, retention_seconds=10.0, monotonic=lambda: clock["now"])
    with c.begin_turn():
        ref = weakref.ref(completed_trace(c))
    clock["now"] = 11.0
    assert c.collect()[TID]["status"] == "unknown"
    gc.collect()
    assert ref() is None


@pytest.mark.platforms("linux")
def test_retain_refuses_foreign_boundary(world):
    c = make_collector(world)
    with c.begin_turn():
        receipt = c.open_trace(TID, RID)
        receipt.ended(RID)
        assert receipt.finish("normal")
    with boundary.turn_boundary(world["manager"], "different-turn"):
        assert c.retain(TID) is None
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_verifier_pins_receipt_and_registration_digests(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c.collect()
    binding = c.expected_binding(TID)
    envelope = c.issue_transfer(TID)
    assert collector_mod.TransferVerifier(world["key"]).verify(
        envelope, expected_binding=binding) is not None
    for pins in (dict(binding, receipt_sha256="0" * 64),
                 dict(binding, registration_sha256="1" * 64)):
        fresh = collector_mod.TransferVerifier(world["key"])
        assert fresh.verify(envelope, expected_binding=pins) is None


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("mutate", ["invalidate", "registration", "late_dispatch", "abandon"])
def test_mid_issue_mutation_refuses_transfer(world, monkeypatch, mutate):
    c = make_collector(world)
    world["context"].register_hook("pre_tool_call", lambda **kw: None)
    with c.begin_turn():
        receipt = completed_trace(c)
        copied = contextvars.copy_context()
    assert c.collect()[TID]["status"] == "collected"
    in_read, release = threading.Event(), threading.Event()
    original = receipts.read_receipt

    def paused(directory, trace_id):
        in_read.set()
        assert release.wait(5)
        return original(directory, trace_id)

    monkeypatch.setattr(receipts, "read_receipt", paused)
    box = {}
    thread = threading.Thread(target=lambda: box.update(envelope=c.issue_transfer(TID)))
    thread.start()
    assert in_read.wait(5)
    if mutate == "invalidate":
        receipt.invalidate()
    elif mutate == "registration":
        world["context"].register_hook("post_llm_call", lambda **kw: None)
    elif mutate == "late_dispatch":
        copied.run(world["manager"].invoke_hook, "pre_tool_call", turn_id=TURN)
    else:
        world["context"]._load_abandoned = True
    release.set()
    thread.join(5)
    assert box["envelope"] is None
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_collect_during_abandonment_reports_unverified_and_latches(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    assert c.collect()[TID]["status"] == "collected"
    world["context"]._load_abandoned = True
    assert c.collect()[TID]["status"] == "unverified"  # cached authority is not re-reported
    world["context"]._load_abandoned = False
    assert c.issue_transfer(TID) is None  # the observed abandonment latched durably
    assert c.collect()[TID]["status"] == "unverified"


@pytest.mark.platforms("linux")
def test_duplicate_reopen_disposes_prior_receipts(world):
    c = make_collector(world, max_traces=2)
    refs = []
    with c.begin_turn():
        refs.append(weakref.ref(c.open_trace(TID, RID)))
        assert c.retain(TID) is not None
        for _ in range(11):
            refs.append(weakref.ref(c.open_trace(TID, RID)))
            c.retain(TID)
    gc.collect()
    assert all(ref() is None for ref in refs[:-1])  # every replaced Receipt is released
    result = c.collect()
    assert result[TID]["status"] == "collected"


@pytest.mark.platforms("linux")
def test_duplicate_reopen_under_capacity_pressure_releases_handles(world):
    c = make_collector(world, max_traces=1)
    with c.begin_turn():
        c.open_trace(TID, RID)
        assert c.retain(TID) is not None
        c.open_trace(TID, RID)  # replaces; the prior handle is orphaned, not lost
        assert c.retain(TID) is not None
    c.collect()
    with boundary._CUSTODY_LOCK:
        assert not boundary._RETAINED


@pytest.mark.platforms("linux")
def test_trace_history_is_bounded(world):
    c = make_collector(world, max_traces=1)
    for i in range(1000):
        trace_id = f"{i:032x}"
        c.open_trace(trace_id, RID)
        assert c.retain(trace_id) is None  # outside a turn
    assert len(c._traces) <= 16
    assert len(c.collect()) <= 16


@pytest.mark.platforms("linux")
def test_disposed_reader_does_not_pin_collector(world):
    clock = {"now": 0.0}
    c = make_collector(world, retention_seconds=10.0, monotonic=lambda: clock["now"])
    with c.begin_turn():
        ref_expired = weakref.ref(completed_trace(c))
        completed_trace(c, TID2, RID2, CID2)
    clock["now"] = 11.0
    result = c.collect()  # lazy expiry plus disposed-handle consumption happen here
    assert result[TID]["status"] == "unknown"
    collector_ref = weakref.ref(c)
    del c
    gc.collect()
    assert collector_ref() is None  # the custody closure pins neither collector nor Receipt
    assert ref_expired() is None


@pytest.mark.platforms("linux")
def test_concurrent_death_refreshes_stale_pending_reports(world, monkeypatch):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
        completed_trace(c, TID2, RID2, CID2)
    original = c._quiescence
    entered, go = threading.Event(), threading.Event()
    count = [0]

    def wrapped():
        result = original()
        if threading.current_thread().name == "reporter":
            count[0] += 1
            if count[0] >= 3:
                entered.set()
                assert go.wait(4)
        return result

    monkeypatch.setattr(c, "_quiescence", wrapped)
    box = {}
    reporter = threading.Thread(name="reporter", target=lambda: box.update(result=c.collect()))
    reporter.start()
    assert entered.wait(4)
    world["context"]._load_abandoned = True
    assert c.issue_transfer(TID2) is None  # concurrent issuer observes and durably kills
    world["context"]._load_abandoned = False
    go.set()
    reporter.join(4)
    assert not reporter.is_alive()
    statuses = {t: r["status"] for t, r in box["result"].items()}
    assert set(statuses.values()) == {"unverified"}, statuses


@pytest.mark.platforms("linux")
def test_malformed_signatures_are_rejected_not_raised(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c.collect()
    envelope = c.issue_transfer(TID)
    binding = c.expected_binding(TID)
    verifier = collector_mod.TransferVerifier(world["key"])
    assert verifier.verify(envelope, expected_binding=binding) is not None
    for bad in ("é" * 32, "🎉", "z" * 64, "ab", 123, None):
        forged = dict(envelope, signature=bad)
        assert collector_mod.TransferVerifier(world["key"]).verify(
            forged, expected_binding=binding) is None, bad


@pytest.mark.platforms("linux")
def test_expected_binding_in_fork_child_with_inherited_lock(world):
    c = make_collector(world)
    with c.begin_turn():
        completed_trace(c)
    c._lock.acquire()  # simulate a parent thread holding the collector lock at fork
    try:
        pid = os.fork()
    except BaseException:
        c._lock.release()
        raise
    if pid == 0:
        signal.signal(signal.SIGALRM, lambda *_: os._exit(97))
        signal.alarm(3)
        try:
            binding = c.expected_binding(TID)  # must not block on the inherited lock
            assert binding["trace_id"] == TID and binding["receipt_sha256"] is None
            os._exit(0)
        except BaseException:
            os._exit(98)
    _, status = os.waitpid(pid, 0)
    c._lock.release()
    assert os.waitstatus_to_exitcode(status) == 0
    assert c.expected_binding(TID)["receipt_sha256"] is None  # nothing collected yet
