"""Telegram cron topic naming and continuable thread persistence."""

import contextlib
import logging
import re
from typing import Optional

logger = logging.getLogger("cron.scheduler")


def _cron_new_telegram_thread_per_output_enabled(config: Optional[dict]) -> bool:
    """Return whether cron should create a fresh Telegram DM topic per output."""
    try:
        return bool(
            (config or {})
            .get("cron", {})
            .get("telegram_new_thread_per_output", False)
        )
    except (AttributeError, TypeError):
        return False



def _cron_output_topic_name(job: dict) -> str:
    """Build a Telegram-safe topic name for one cron output."""
    raw_name = str(job.get("name") or job.get("id") or "cron")
    clean_name = re.sub(r"\s+", " ", raw_name).strip() or "cron"
    job_id = str(job.get("id") or "")[:8]
    from cron import scheduler as _sched

    try:
        timestamp = _sched._hermes_now().astimezone().strftime("%m-%d %H:%M:%S UTC")
    except (OSError, ValueError):
        timestamp = "now UTC"
    prefix = "Cron: "
    suffix = f" · {job_id} · {timestamp}" if job_id else f" · {timestamp}"
    room = max(8, 128 - len(prefix) - len(suffix))
    if len(clean_name) > room:
        clean_name = clean_name[: max(0, room - 1)].rstrip() + "…"
    return f"{prefix}{clean_name}{suffix}"[:128]



def _maybe_assign_fresh_telegram_cron_thread(
    job: dict,
    target: dict,
    config: Optional[dict],
    *,
    can_create_named_dm_topic: bool = True,
) -> dict:
    """Replace a private Telegram target's thread with a fresh topic name."""
    if not can_create_named_dm_topic:
        return target
    if not _cron_new_telegram_thread_per_output_enabled(config):
        return target
    if str(target.get("platform", "")).lower() != "telegram":
        return target

    from gateway.delivery import looks_like_telegram_private_chat_id

    chat_id = target.get("chat_id")
    if not looks_like_telegram_private_chat_id(
        str(chat_id) if chat_id is not None else None
    ):
        return target

    adjusted = dict(target)
    adjusted["thread_id"] = _cron_output_topic_name(job)
    return adjusted



def _persist_cron_thread_binding(job: dict, platform_name: str, chat_id: str, thread_id: str) -> None:
    """Pin a freshly-created continuable thread into the job's origin target.

    Without this, a ``deliver: origin`` job whose ``origin.thread_id`` is null
    opens a NEW named topic on every non-silent delivery (topic spam) and the
    null-thread fallback can deliver into the chat's warm flat session instead
    of a dedicated lane. Persisting the created thread makes the second and
    later deliveries explicit routed targets (the scheduler's "never override
    an explicit origin thread" guard then skips thread creation entirely).

    Best-effort: a persistence failure never fails a delivery that already
    succeeded — it is logged and the next delivery retries the pin.
    """
    try:
        from cron.jobs import update_job

        origin = dict(job.get("origin") or {})
        if str(origin.get("thread_id") or ""):
            return  # already pinned — never override an explicit thread
        if str(origin.get("platform", "")).lower() != str(platform_name).lower():
            return
        if str(origin.get("chat_id", "")) != str(chat_id):
            return
        origin["thread_id"] = str(thread_id)
        update_job(job["id"], {"origin": origin})
        logger.info(
            "Job '%s': pinned continuable thread %s:%s:%s into origin",
            job.get("id", "?"), platform_name, chat_id, thread_id,
        )
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
        logger.warning(
            "Job '%s': failed to pin continuable thread %s:%s:%s into origin: %s",
            job.get("id", "?"), platform_name, chat_id, thread_id, e,
        )

def _resolve_cron_surface_mode(pconfig, logical_platform_name: str) -> str:
    """Return ``"in_channel"`` or ``"thread"`` (default) for a platform config.

    Native: flat ``platforms.<p>.extra.cron_continuable_surface``. Relay-fronted:
    ``platforms.relay.extra.<logical>.cron_continuable_surface`` (same sub-block as the relay's
    Slack knobs); the sub-block wins and is scoped to its logical platform. Unlike
    _relay_slack_extra, a sub-block that omits the knob falls back to the flat key (deliberate —
    the flat key must keep working), so a flat value applies to EVERY platform the relay fronts.
    """
    with contextlib.suppress(Exception):
        extra = getattr(pconfig, "extra", None) or {}
        sub = extra.get(str(logical_platform_name or "").lower())
        raw = sub.get("cron_continuable_surface") if isinstance(sub, dict) else None
        if raw is None:
            raw = extra.get("cron_continuable_surface")
        if raw is not None and str(raw).strip().lower() == "in_channel":
            return "in_channel"
    return "thread"
