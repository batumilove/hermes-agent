"""Bounded Feishu callback authentication (public GHSA-pmqc-57g8-c22c).

Adapted from upstream PR73406 first prefix cbd561467b90217ce2434af79bb50587223f8bb5.
The adapter keeps existing configuration and optional SDK/import boundaries.
"""
from __future__ import annotations
import hmac
import json
from typing import Any


class InvalidFeishuMessagePayload(ValueError):
    """Message content is unsafe to normalize recursively."""


class WebhookRejection(ValueError):
    """Controlled ingress rejection that the adapter renders as its HTTP response."""
    def __init__(self, anomaly, status, text="Unauthorized", json_msg=None):
        super().__init__(anomaly)
        self.anomaly, self.status = anomaly, status
        self.text, self.json_msg = text, json_msg


def json_containers_within_depth(value: Any, max_depth: int) -> bool:
    """Check container depth iteratively so validation cannot recurse."""
    stack = [(iter((value,)), 0)]
    while stack:
        values, parent_depth = stack[-1]
        try:
            item = next(values)
        except StopIteration:
            stack.pop()
            continue
        if not isinstance(item, (dict, list)):
            continue
        depth = parent_depth + 1
        if depth > max_depth:
            return False
        children = item.values() if isinstance(item, dict) else item
        stack.append((iter(children), depth))
    return True


def authenticate_webhook_body(adapter, headers, body_bytes):
    """Authenticate exact body bytes before charging the delivery quota."""
    from . import adapter as feishu
    signature_valid = False
    if adapter._encrypt_key and headers.get("x-lark-signature"):
        signature_valid = adapter._is_webhook_signature_valid(headers, body_bytes)
        if not signature_valid:
            raise WebhookRejection("401-sig", 401)
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
        if not isinstance(payload, dict) or not json_containers_within_depth(payload, feishu._FEISHU_WEBHOOK_MAX_JSON_DEPTH):
            raise ValueError("invalid envelope")
    except (ValueError, UnicodeError, RecursionError):
        raise WebhookRejection("400", 400, json_msg="invalid json") from None
    if "encrypt" in payload:
        raise WebhookRejection("400-encrypted", 400, json_msg="encrypted webhook payloads are not supported")
    header = payload.get("header")
    if header is None:
        header = {}
    elif not isinstance(header, dict):
        raise WebhookRejection("400", 400, json_msg="invalid header")
    is_url_verification = payload.get("type") == "url_verification"
    if is_url_verification and not adapter._verification_token:
        raise WebhookRejection("401-token", 401)
    if adapter._verification_token:
        incoming_token = str(header.get("token") or payload.get("token") or "")
        try:
            token_valid = hmac.compare_digest(incoming_token.encode("utf-8"), adapter._verification_token.encode("utf-8"))
        except UnicodeEncodeError:
            token_valid = False
        if not token_valid:
            raise WebhookRejection("401-token", 401)
    if adapter._encrypt_key and not is_url_verification and not signature_valid:
        raise WebhookRejection("401-sig", 401)
    return payload, header, is_url_verification


def record_webhook_anomaly(self, remote_ip: str, status: str) -> None:
    """Count repeated failures in a capped source table, sweeping at most once a minute."""
    from . import adapter as feishu
    now = feishu.time.time()
    entry = self._webhook_anomaly_counts.get(remote_ip)
    if entry is not None:
        count, _last_status, first_seen = entry
        if now - first_seen < feishu._FEISHU_WEBHOOK_ANOMALY_TTL_SECONDS:
            count += 1
            if count % feishu._FEISHU_WEBHOOK_ANOMALY_THRESHOLD == 0:
                feishu.logger.warning(
                    "[Feishu] Webhook anomaly: %d consecutive error responses (%s) from %s "
                    "over the last %.0fs",
                    count,
                    status,
                    remote_ip,
                    now - first_seen,
                )
            self._webhook_anomaly_counts[remote_ip] = (count, status, first_seen)
            return
        del self._webhook_anomaly_counts[remote_ip]

    if len(self._webhook_anomaly_counts) >= feishu._FEISHU_WEBHOOK_ANOMALY_MAX_KEYS:
        if now < self._webhook_anomaly_next_sweep_at:
            return
        self._webhook_anomaly_next_sweep_at = (
            now + feishu._FEISHU_WEBHOOK_ANOMALY_SWEEP_INTERVAL_SECONDS
        )
        expired_ips = [
            ip
            for ip, (_count, _status, first_seen) in self._webhook_anomaly_counts.items()
            if now - first_seen >= feishu._FEISHU_WEBHOOK_ANOMALY_TTL_SECONDS
        ]
        for ip in expired_ips:
            del self._webhook_anomaly_counts[ip]

        if len(self._webhook_anomaly_counts) >= feishu._FEISHU_WEBHOOK_ANOMALY_MAX_KEYS:
            return

    # First occurrence, or a replacement after the prior entry expired.
    self._webhook_anomaly_counts[remote_ip] = (1, status, now)
