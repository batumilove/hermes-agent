"""Send acknowledged saved finals through their durable bot and profile identity."""

import asyncio
from functools import partial
from pathlib import Path
import sqlite3

from gateway import delivery_recovery as recovery
from gateway.config import Platform

_DB_ERRORS = (ValueError, sqlite3.Error, OSError, RuntimeError)


def _admitted(runner):
    controller = getattr(runner, "_post_response_controller", None)
    return (getattr(runner, "_running", False)
            and not getattr(runner, "_executor_closing", False)
            and (controller is None or not controller._stop.is_set()))


async def _db_call(runner, operation, *args, **kwargs):
    # Every SQLite operation participates in the gateway's writer census and
    # executor-closing fence, including writes after a cancelled send.
    return await runner._run_in_executor_with_context(partial(operation, *args, **kwargs))


def _read_requests(db_path, after):
    with recovery._connection(db_path) as (_, conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_requests'").fetchone():
            return [], ''
        has_executions = conn.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_recovery_executions'").fetchone()
        unspent = ('''AND NOT EXISTS (SELECT 1 FROM delivery_recovery_executions e
            WHERE e.request_id=r.request_id)''' if has_executions else '')
        rows = [dict(row) for row in conn.execute('''SELECT r.request_id, r.obligation_id
            FROM delivery_recovery_requests r
            JOIN delivery_obligations o ON o.obligation_id=r.obligation_id
            WHERE r.request_id > ? ''' + unspent + ''' ORDER BY r.request_id LIMIT 32''', (after,))]
    return rows, rows[-1]['request_id'] if len(rows) == 32 else ''


def _read_receipt(db_path, obligation_id):
    with recovery._connection(db_path) as (path, conn):
        receipt, _, _ = recovery._snapshot(path, conn, obligation_id)
        return receipt


def _adapter(runner, receipt):
    # Adapter ownership is independent of the DB's runtime profile (shared
    # bots can serve another profile). Missing ownership must never fall back.
    profile = receipt.get('adapter_profile')
    if not isinstance(profile, str) or not profile.strip():
        return None
    return runner._authorization_adapter(Platform(receipt['platform']), profile)


async def _reconcile_completed(runner, db_path):
    pending = getattr(runner, '_delivery_recovery_completed', {})
    matching = [(key, claim) for key, claim in list(pending.items()) if key[0] == str(db_path)]
    for key, claim in matching[:32]:
        for _ in range(3):
            try:
                await _db_call(runner, recovery._retire_completed, db_path, claim)
            except _DB_ERRORS:
                continue
            pending.pop(key, None)
            break
    return any(key[0] == str(db_path) for key in pending)


async def _complete(runner, db_path, claim, delivered):
    pending = getattr(runner, '_delivery_recovery_completed', None)
    if pending is None:
        pending = runner._delivery_recovery_completed = {}
    key = (str(db_path), claim['request_id'])
    pending[key] = claim
    try:
        settled = await _db_call(runner, recovery.settle_retry, db_path, claim, delivered=delivered)
    except _DB_ERRORS:
        settled = False
    if settled:
        pending.pop(key, None)
        controller = getattr(runner, '_post_response_controller', None)
        if delivered and controller is not None:
            controller.wake()
    else:
        await _reconcile_completed(runner, db_path)


async def _send_claim(runner, db_path, claim):
    from gateway.run import _async_profile_runtime_scope

    receipt = claim['receipt']
    delivered = False
    try:
        # Empty database polls never hydrate credentials or terminal policy.
        async with _async_profile_runtime_scope(Path(db_path).parent):
            adapter = _adapter(runner, receipt)
            if _admitted(runner) and adapter is not None:
                result = await adapter.send(
                    chat_id=receipt['chat_id'],
                    content='♻️ Operator-requested retry — possible duplicate:\n\n' + receipt['content'],
                    metadata={'thread_id': receipt['thread_id']} if receipt['thread_id'] else None)
                delivered = result is not None and getattr(result, 'success', False) is True
    finally:
        # Only an unwound send may retire its execution. If the closing fence
        # refuses settlement, the abandoned receipt remains safely ambiguous.
        await _complete(runner, db_path, claim, delivered)


async def _consume_request(runner, db_path, request):
    try:
        receipt = await _db_call(runner, _read_receipt, db_path, request['obligation_id'])
        if not _admitted(runner) or _adapter(runner, receipt) is None:
            return
        claim = await _claim_request(runner, db_path, request['request_id'])
    except _DB_ERRORS:
        return  # Invalid/stale requests must not hide later decisions.
    if claim is not None:
        await _send_claim(runner, db_path, claim)


async def _claim_request(runner, db_path, request_id):
    pending = asyncio.create_task(_db_call(runner, recovery.claim_retry, db_path, request_id))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        # A cancellation can race SQLite's COMMIT. Account for the committed
        # claim without entering the network boundary; the executor census
        # keeps DB close behind this writer even if cancellation repeats.
        await asyncio.wait({pending})
        if not pending.cancelled() and pending.exception() is None and pending.result() is not None:
            await _complete(runner, db_path, pending.result(), False)
        raise


async def consume_requests(runner, db_path):
    """One bounded page on the owning loop; no replay without a fresh decision."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    if asyncio.get_running_loop() is not getattr(runner, '_gateway_loop', None):
        raise ValueError('recovery must run on the owning gateway event loop')
    if not _admitted(runner):
        return
    token = set_hermes_home_override(Path(db_path).parent)
    try:
        if await _reconcile_completed(runner, db_path):
            return
        cursors = getattr(runner, '_delivery_recovery_cursors', None)
        if cursors is None:
            cursors = runner._delivery_recovery_cursors = {}
        key = str(db_path)
        requests, cursors[key] = await _db_call(runner, _read_requests, db_path, cursors.get(key, ''))
        for request in requests:
            if not _admitted(runner):
                return
            await _consume_request(runner, db_path, request)
            if any(path == key for path, _ in getattr(runner, '_delivery_recovery_completed', {})):
                return
    finally:
        reset_hermes_home_override(token)
