"""Bounded Telegram thread retries without discarding a viable private reply anchor."""


def _private_reply_anchor(chat_id, reply_to_id, thread_kwargs):
    return (
        str(chat_id).isdigit() and int(chat_id) > 0
        and str(reply_to_id).isdigit() and int(reply_to_id) > 0
        and not thread_kwargs.get("direct_messages_topic_id"))


def retry_missing_thread(
    adapter, error, chat_id, reply_to_id, metadata, private_dm_topic_send,
    effective_thread_id, thread_kwargs, used_thread_fallback, retried_thread_not_found,
):
    """Return the next chunk route or a terminal refusal; never strip a private reply anchor."""
    from plugins.platforms.telegram.adapter import SendResult, _redact_telegram_error_text, logger

    refusal = SendResult(success=False, error=_redact_telegram_error_text(error), retryable=False)
    if private_dm_topic_send and used_thread_fallback:
        return refusal
    if effective_thread_id is None:
        raise error
    created_for_send = (metadata or {}).get("telegram_dm_topic_created_for_send")
    if private_dm_topic_send:
        if _private_reply_anchor(chat_id, reply_to_id, thread_kwargs) and not created_for_send:
            # Carry fallback state across chunks; _chunk_reply_routing retains each reply anchor.
            return None, {}, True, retried_thread_not_found
        return refusal
    if created_for_send:
        return refusal
    if not retried_thread_not_found:
        logger.warning("[%s] Thread %s not found, retrying once with same thread_id", adapter.name, effective_thread_id)
        return effective_thread_id, thread_kwargs, used_thread_fallback, True
    logger.warning("[%s] Thread %s not found, retrying without message_thread_id", adapter.name, effective_thread_id)
    adapter._prune_stale_dm_topic_binding(chat_id, effective_thread_id, metadata=metadata)
    return None, {"message_thread_id": None}, True, retried_thread_not_found


async def send_control_with_thread_fallback(adapter, **kwargs):
    """Control sends retain their one-retry policy and keep private anchored bindings intact."""
    from plugins.platforms.telegram.adapter import _await_with_thread_deadline, _TEXT_SEND_DEADLINE, logger

    if not adapter._bot:
        raise RuntimeError("Not connected")
    message_thread_id = kwargs.get("message_thread_id")
    try:
        return await _await_with_thread_deadline(
            adapter._bot.send_message(**kwargs), timeout=_TEXT_SEND_DEADLINE, label="telegram-send", dump_on_blocked_loop=False)
    except Exception as error:
        if not (message_thread_id is not None and adapter._is_bad_request_error(error) and adapter._is_thread_not_found_error(error)):
            raise
        logger.warning(
            "[%s] Thread %s not found for control message, retrying without message_thread_id", adapter.name, message_thread_id)
        if not _private_reply_anchor(kwargs.get("chat_id"), kwargs.get("reply_to_message_id"), kwargs):
            adapter._prune_stale_dm_topic_binding(kwargs.get("chat_id"), message_thread_id)
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("message_thread_id", None)
        return await _await_with_thread_deadline(
            adapter._bot.send_message(**retry_kwargs), timeout=_TEXT_SEND_DEADLINE, label="telegram-send", dump_on_blocked_loop=False)
