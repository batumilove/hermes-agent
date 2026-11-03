"""Per-request Mattermost failures; an unsuccessful POST may already have delivered."""
from __future__ import annotations

import json
import math
import re

from gateway.platforms.base import SendResult


class ApiFailure(dict):
    """Falsey API result compatible with existing dict consumers, with local failure metadata."""

    def __init__(self, status=None, body='', retry_after=None):
        super().__init__()
        self.status = status
        self.body = body
        self.retry_after = None
        try:
            delay = float(retry_after)
            if math.isfinite(delay) and delay >= 0:
                self.retry_after = delay
        except (TypeError, ValueError):
            pass

    def send_result(self, error):
        kind = {401: 'forbidden', 403: 'forbidden', 404: 'not_found', 429: 'rate_limited'}.get(self.status, 'unknown')
        try:
            value = json.loads(self.body)
            code = value.get('id', '') if isinstance(value, dict) else ''
        except (ValueError, TypeError):
            code = ''
        code = code if isinstance(code, str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,160}', code) else ''
        # Do not expose response bodies (which may contain user data), or mark an
        # ambiguous POST retry-safe. Only a rate-limit refusal authorizes a retry.
        return SendResult(success=False, error=f'{error} (Mattermost HTTP {self.status or "unknown"})',
                          error_kind=kind, retryable=self.status == 429,
                          retry_after=self.retry_after if self.status == 429 else None,
                          raw_response={'mattermost_failure': True, 'http_status': self.status,
                                        'error_id': code, 'delivery_uncertain': self.status is None or self.status >= 500})

    def broken_thread_root(self):
        if self.status not in {400, 404}:
            return False
        body = self.body.lower()
        return (any(word in body for word in ('root_id', 'rootid', 'root id', 'thread', 'post'))
                and any(word in body for word in ('invalid', 'not found', 'does not exist', 'missing')))
