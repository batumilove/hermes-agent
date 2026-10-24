"""Plain-language copy for the one-line cron failure notice delivered to a job's chat.

The scheduler classifies the failure text through ``agent.error_classifier.classify_api_error``
(one classifier for the whole app, no cron-local regex ladder) and looks the verdict up here.
Every notice says WHAT happened and WHAT TO DO, and names the exact ``hermes cron`` command plus
the real output directory — "cron output" alone sent operators hunting.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from hermes_constants import display_hermes_home


def cron_output_dir_display(job_id: str) -> str:
    """User-facing path of a job's saved run output (profile-aware)."""
    return f"{display_hermes_home()}/cron/output/{job_id}/"


_HTTP_STATUS_IN_TEXT = re.compile(r"(?:\bHTTP\b|\bError code\b|\bstatus(?: code)?\b)\W{0,3}(\b[45]\d\d\b)", re.IGNORECASE)
_LEADING_EXC_TYPE = re.compile(r"^(?:[\w.]+\.)?([A-Z]\w*(?:Error|Timeout|Exception))\s*:")


def classify_cron_failure_reason(text: str) -> str:
    """``FailoverReason`` value for a cron failure string (``"unknown"`` when unclassifiable).

    The scheduler only has ``str(exc)``, so the status code and exception type the classifier
    keys on are rebuilt from the text: a whole-token HTTP code after ``HTTP`` / ``Error code`` /
    ``status`` (a bare ``429`` inside a job id or hash never counts, #83188) and a leading
    ``ReadTimeout:``-style type prefix."""
    from agent.error_classifier import classify_api_error

    status = _HTTP_STATUS_IN_TEXT.search(text)
    type_name = _LEADING_EXC_TYPE.match(text)
    exc_cls = type(type_name.group(1), (Exception,), {}) if type_name else Exception
    exc = exc_cls(text)
    if status:
        exc.status_code = int(status.group(1))
    return classify_api_error(exc).reason.value


# What happened, per reason: the one gloss table shared with subagent notices lives in
# agent/turn_failure_copy.py so the two never drift; the job is the subject here.
def _provider_failure_cause(reason: str) -> Optional[str]:
    from agent.turn_failure_copy import failure_cause_gloss

    return failure_cause_gloss(reason, subject="this job", possessive="the job's")


_TRANSIENT_REASONS = frozenset({"timeout", "rate_limit", "upstream_rate_limit", "overloaded", "server_error"})

# Reason -> what to do. Transient reasons get the backup-provider clause from the scheduler
# (it knows whether a fallback chain is configured) instead of a fixed sentence.
_PROVIDER_FAILURE_ACTION: dict[str, str] = {
    "billing": (
        "Top up or wait for the limit to reset, or pin another provider with "
        "`hermes cron edit {job_id} --provider <name>`."
    ),
    "auth": (
        "Sign in again with /login (or `{relogin}` in a terminal), or pin a "
        "working provider with `hermes cron edit {job_id} --provider <name>`, then "
        "`hermes cron run {job_id}` to retry."
    ),
    "model_not_found": "Pick another model with `hermes cron edit {job_id} --model <name>`.",
    "upstream_blocked": (
        "A firewall in front of the provider blocked the request (not your key): set a User-Agent "
        "via `extra_headers` on the provider's custom_providers entry, or pin another provider with "
        "`hermes cron edit {job_id} --provider <name>`."
    ),
    "context_overflow": "Shorten the job's prompt with `hermes cron edit {job_id} --prompt <text>`.",
}
_PROVIDER_FAILURE_ACTION["auth_permanent"] = _PROVIDER_FAILURE_ACTION["auth"]
_PROVIDER_FAILURE_ACTION["billing_unverified"] = _PROVIDER_FAILURE_ACTION["billing"]
_PROVIDER_FAILURE_ACTION["payload_too_large"] = _PROVIDER_FAILURE_ACTION["context_overflow"]
_PROVIDER_FAILURE_ACTION["content_policy_blocked"] = (
    "Reword the job's prompt with `hermes cron edit {job_id} --prompt <text>`, or pick another "
    "model with `hermes cron edit {job_id} --model <name>`."
)
_PROVIDER_FAILURE_ACTION["provider_policy_blocked"] = (
    "Retrying won't help: check the account's status and data/privacy settings with the provider, "
    "or pin another model with `hermes cron edit {job_id} --model <name>`."
)
_DEFAULT_FAILURE_ACTION = "Run it again with `hermes cron run {job_id}`, or edit it with `hermes cron edit {job_id}`."


def provider_failure_notice(
    job_name: str, job_id: str, reason: str, *, backup_provider_phrase: str, provider: Any = None,
) -> Optional[str]:
    """The notice for a provider-shaped ``reason``, or None when the reason is not one.
    ``provider`` is the job's pinned slug (if any) so the auth action names its exact sign-in."""
    cause = _provider_failure_cause(reason)
    if cause is None:
        return None
    if reason in _TRANSIENT_REASONS:
        action = (
            f"{backup_provider_phrase} It will run again at its next scheduled time; "
            f"`hermes cron run {job_id}` tries now."
        )
    else:
        from agent.turn_failure_copy import relogin_command_hint

        action = _PROVIDER_FAILURE_ACTION.get(reason, _DEFAULT_FAILURE_ACTION).format(
            job_id=job_id, relogin=relogin_command_hint(provider))
    return (
        f"⚠️ Cron '{job_name}' failed: {cause}. {action} "
        f"Run log: `hermes cron runs {job_id}`."
    )


def generic_failure_notice(job_name: str, job_id: str, cleaned_error: str) -> str:
    """Unclassified failure: the cleaned error text plus where to look and what to do."""
    return (
        f"⚠️ Cron '{job_name}' failed: {cleaned_error}. "
        f"See the full run with `hermes cron runs {job_id}` (output saved under "
        f"{cron_output_dir_display(job_id)}); run it again with `hermes cron run {job_id}`, "
        f"edit it with `hermes cron edit {job_id}`, or pause it with `hermes cron pause {job_id}`."
    )


def script_timeout_notice(job_name: str, job_id: str) -> str:
    return (
        f"⚠️ Cron '{job_name}' failed: its script timed out. No model was invoked. "
        f"Check the script's output under {cron_output_dir_display(job_id)} or `hermes cron runs {job_id}`, "
        f"then run it again with `hermes cron run {job_id}`."
    )


def inactivity_notice(job_name: str, job_id: str) -> str:
    return (
        f"⚠️ Cron '{job_name}' failed: the job stalled — it stopped doing anything for too long "
        f"and was cut off. Check what it was doing in the saved output under "
        f"{cron_output_dir_display(job_id)} (`hermes cron runs {job_id}`), then run it again with "
        f"`hermes cron run {job_id}`."
    )


def blocked_config_notice(job_name: str, reason: str) -> str:
    """One-time notice when the pre-run configuration check refused to start the job."""
    reason = reason.rstrip()
    if reason and reason[-1] not in ".!?":
        reason += "."
    return (
        f"⛔ Cron '{job_name}' did not run: {reason} Nothing was charged. Hermes will try again at "
        "the next scheduled time and will not repeat this alert; check with "
        "`hermes cron doctor`."
    )


def _classify_no_agent_script_failure(job_name: str, text: str) -> str:
    """Classify a ``no_agent=True`` script failure for chat delivery.

    ``no_agent`` jobs never touch a provider/fallback chain — the script IS
    the job. So every provider classifier (rate-limit, timeout, auth) is
    semantically wrong here, even when the raw error string happens to
    contain ``timeout``, ``429``, or ``authentication`` (those tokens
    routinely appear *inside* the script's own stdout/JSON).

    Produces a bounded, sanitized script-failure message. When the script
    emitted a safe structured ``kind`` (e.g. ``collector_timeout``) we
    surface it so the operator knows *what* broke, alongside any
    ``timeout_seconds``. Malformed / oversized stdout is truncated and
    never claims provider involvement.
    """
    import json as _json

    lower = text.lower()

    # A script-execution timeout signal (subprocess ``SIGTERM``/exit 124, an
    # explicit scheduler timeout phrase, or a structured timeout ``kind``).
    # Do not infer execution timeout from arbitrary service output such as
    # ``DNS timeout/unreachable`` or configured limits like ``timeout=25s``
    # emitted by a script that exited normally.
    is_timeout = (
        "script timed out after" in lower
        or "command timed out after" in lower
        or "subprocess.timeoutexpired" in lower
        or "collector_timeout" in lower
        or "exit code 124" in lower
        or "exited with code 124" in lower
        or "signal 15" in lower
    )

    # Try to lift a safe structured ``kind`` (+ optional ``timeout_seconds``)
    # from the first JSON object on the script's stdout. Only short, safe
    # scalar fields are surfaced — never nested blobs.
    kind: str | None = None
    timeout_seconds: str | None = None
    # Bound the scan window so a multi-KB stdout blob is never fully parsed.
    for candidate in _extract_json_candidates(text[:4000]):
        try:
            obj = _json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        k = obj.get("kind")
        if isinstance(k, str) and k and _is_safe_kind(k):
            kind = k
        ts = obj.get("timeout_seconds")
        if (
            isinstance(ts, (int, float))
            and not isinstance(ts, bool)
            and math.isfinite(float(ts))
            and 0 <= float(ts) <= 31_536_000
        ):
            timeout_seconds = str(int(ts))
        if kind:
            break

    # Preserve timeout treatment for safe structured kinds beyond the common
    # ``collector_timeout`` spelling without scanning arbitrary prose.
    if kind and "timeout" in kind.lower():
        is_timeout = True

    if kind:
        detail = f"script failure: {kind}"
        if timeout_seconds:
            detail += f" (timeout {timeout_seconds}s)"
    elif is_timeout:
        detail = "script timed out"
    else:
        # Prefer concise operational evidence over wrapper boilerplate. Ratio
        # mismatches (for example cameras_online=2/3) explain the impact, and
        # the final FAILED line explains where the script stopped.
        lines = [re.sub(r"\s+", " ", line).strip() for line in text[:4000].splitlines()]
        lines = [line for line in lines if line]

        def _has_degraded_ratio(line: str) -> bool:
            for numerator, denominator in re.findall(r"\b\w+=([0-9]+)/([0-9]+)\b", line):
                if int(numerator) < int(denominator):
                    return True
            return False

        degraded = [line for line in lines if _has_degraded_ratio(line)]
        failed = [
            line
            for line in lines
            if "failed" in line.lower()
            and not line.lower().startswith("script exited with code")
        ]
        selected: list[str] = []
        if degraded:
            selected.append(degraded[0])
        if failed:
            selected.append(failed[-1])
        selected = [re.sub(r"^[A-Z][A-Z0-9_]{2,}\s+", "", line) for line in selected]
        cleaned = "; ".join(dict.fromkeys(selected))
        if not cleaned:
            cleaned = re.sub(r"\s+", " ", text[:2000]).strip()
        if len(cleaned) > 120:
            cleaned = cleaned[:117].rstrip() + "..."
        detail = f"script failed: {cleaned}" if cleaned else "script failed"

    no_model_clause = " No model was invoked." if is_timeout else ""
    return (
        f"⚠️ Cron '{job_name}' failed: {detail}."
        f"{no_model_clause} Full output saved in cron output."
    )


def _extract_json_candidates(text: str) -> list[str]:
    """Yield plausible JSON object substrings from ``text``.

    A no_agent error looks like ``Script exited with code 1\\nstdout:\\n{...}``.
    We scan for ``{`` ... matching ``}`` spans. Bounded to the first few to
    avoid quadratic scans on adversarial input.
    """
    candidates: list[str] = []
    search_from = 0
    while len(candidates) < 3:
        start = text.find("{", search_from)
        if start == -1:
            break
        depth = 0
        end = start
        in_str = False
        escape = False
        for i in range(start, min(len(text), start + 4000)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if depth == 0 and end > start:
            candidates.append(text[start:end])
            search_from = end
        else:
            break
    return candidates


def _is_safe_kind(kind: str) -> bool:
    """Only allow ``[a-z0-9_]+`` kind tokens into the delivery message.

    Prevents a malicious/buggy script from injecting arbitrary formatting or
    control characters via the ``kind`` field.
    """
    return bool(re.fullmatch(r"[a-z0-9_]{1,48}", kind))
