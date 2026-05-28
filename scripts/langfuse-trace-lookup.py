#!/usr/bin/env python3
"""langfuse-trace-lookup — Operator CLI for finding Hermes traces in Langfuse.

Queries the Langfuse API for traces matching session_id, tags, or metadata
fields, then prints a concise summary suitable for operator workflows.

Usage:
    # By session ID
    python scripts/langfuse-trace-lookup.py --session-id 20260528_120231_dcda03

    # By tag
    python scripts/langfuse-trace-lookup.py --tag platform:telegram

    # By task_id
    python scripts/langfuse-trace-lookup.py --task-id t_6ee1a37c

    # Combined filters
    python scripts/langfuse-trace-lookup.py --tag provider:openrouter --limit 10

    # JSON output for piping
    python scripts/langfuse-trace-lookup.py --session-id sess-1 --json

Environment:
    HERMES_LANGFUSE_PUBLIC_KEY  or LANGFUSE_PUBLIC_KEY  — Langfuse public key
    HERMES_LANGFUSE_SECRET_KEY  or LANGFUSE_SECRET_KEY  — Langfuse secret key
    HERMES_LANGFUSE_BASE_URL    or LANGFUSE_BASE_URL    — Langfuse server URL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import base64


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_langfuse_creds() -> tuple[str, str, str]:
    pk = _env("HERMES_LANGFUSE_PUBLIC_KEY") or _env("LANGFUSE_PUBLIC_KEY")
    sk = _env("HERMES_LANGFUSE_SECRET_KEY") or _env("LANGFUSE_SECRET_KEY")
    base = _env("HERMES_LANGFUSE_BASE_URL") or _env("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com"
    if not (pk and sk):
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set", file=sys.stderr)
        sys.exit(1)
    return pk, sk, base.rstrip("/")


def _langfuse_get(url: str, pk: str, sk: str) -> dict:
    """Make an authenticated GET request to the Langfuse API."""
    creds = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    req = Request(url, headers={
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"HTTP {e.code} from {url}: {body[:500]}", file=sys.stderr)
        sys.exit(2)
    except URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        sys.exit(2)


def _format_ts(ts: str) -> str:
    """Format an ISO timestamp for human-readable output."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts


def _fetch_traces(base_url: str, pk: str, sk: str, *,
                  session_id: str = "", tags: list[str] | None = None,
                  limit: int = 10, from_timestamp: str = "") -> list[dict]:
    """Fetch traces from Langfuse matching filters."""
    params: dict[str, str | list[str]] = {}
    if session_id:
        params["sessionId"] = session_id
    if tags:
        params["tags"] = tags
    if from_timestamp:
        params["fromTimestamp"] = from_timestamp
    params["limit"] = str(limit)
    params["orderBy"] = "timestamp.desc"

    url = f"{base_url}/api/public/traces?{urlencode(params, doseq=True)}"
    data = _langfuse_get(url, pk, sk)
    return data.get("data", [])


def _fetch_trace_detail(base_url: str, pk: str, sk: str, trace_id: str) -> dict:
    """Fetch full trace with observations."""
    url = f"{base_url}/api/public/traces/{trace_id}"
    return _langfuse_get(url, pk, sk)


def _print_summary(traces: list[dict], base_url: str, verbose: bool = False) -> None:
    """Print a human-readable trace summary."""
    if not traces:
        print("No traces found.")
        return

    print(f"Found {len(traces)} trace(s):\n")
    for t in traces:
        tid = t.get("id", "?")
        name = t.get("name", "?")
        session = t.get("sessionId", "")
        ts = _format_ts(t.get("timestamp", ""))
        meta = t.get("metadata", {})
        tags = t.get("tags", [])
        observations = t.get("observations", [])

        # Extract key metadata fields
        platform = meta.get("platform", "")
        provider = meta.get("provider", "")
        model = meta.get("model", "")
        task_id = meta.get("task_id", "")

        print(f"  Trace: {name}")
        print(f"    ID:       {tid}")
        print(f"    Time:     {ts}")
        if session:
            print(f"    Session:  {session}")
        if task_id:
            print(f"    Task:     {task_id}")
        if platform:
            print(f"    Platform: {platform}")
        if provider:
            print(f"    Provider: {provider}")
        if model:
            print(f"    Model:    {model}")
        if tags:
            print(f"    Tags:     {', '.join(tags)}")

        # Observation summary
        gen_obs = [o for o in observations if o.get("type") == "GENERATION"]
        tool_obs = [o for o in observations if o.get("type") == "TOOL"]
        if gen_obs:
            gen = gen_obs[0]
            usage = gen.get("usage", {})
            resp_model = gen.get("metadata", {}).get("response_model", "")
            inp = usage.get("input", 0)
            out = usage.get("output", 0)
            print(f"    LLM:      {resp_model or model} ({inp} in / {out} out tokens)")
        if tool_obs:
            tool_names = [o.get("name", "?").replace("Tool: ", "") for o in tool_obs]
            print(f"    Tools:    {', '.join(tool_names)}")

        # Dashboard link
        print(f"    Link:     {base_url}/trace/{tid}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Look up Hermes traces in Langfuse by session_id, tags, or task_id",
    )
    parser.add_argument("--session-id", "-s", help="Filter by session ID")
    parser.add_argument("--task-id", "-t", help="Filter by task_id (searches metadata)")
    parser.add_argument("--tag", action="append", dest="tags", help="Filter by tag (repeatable)")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max traces to return (default: 10)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show observation details")
    args = parser.parse_args()

    if not any([args.session_id, args.task_id, args.tags]):
        parser.error("At least one filter is required: --session-id, --task-id, or --tag")

    pk, sk, base_url = _get_langfuse_creds()

    # If filtering by task_id, we can't query metadata directly through the
    # API, so we use a tag-based approach or session_id and then filter.
    # Langfuse API doesn't support metadata filtering, so we fetch by tags
    # and filter client-side when task_id is specified.
    if args.task_id and not args.session_id and not args.tags:
        # Add hermes tag to narrow results
        effective_tags = ["hermes"]
    else:
        effective_tags = args.tags

    traces = _fetch_traces(
        base_url, pk, sk,
        session_id=args.session_id or "",
        tags=effective_tags,
        limit=args.limit * 3 if args.task_id else args.limit,  # overfetch for client-side filtering
    )

    # Client-side task_id filtering
    if args.task_id:
        traces = [t for t in traces if t.get("metadata", {}).get("task_id") == args.task_id]
        traces = traces[:args.limit]

    # Fetch detail for each trace to get observations (if not already included)
    if args.verbose or args.json:
        detailed = []
        for t in traces:
            try:
                detail = _fetch_trace_detail(base_url, pk, sk, t["id"])
                detailed.append(detail)
            except Exception:
                detailed.append(t)  # fallback to what we have
        traces = detailed

    if args.json:
        print(json.dumps(traces, indent=2))
    else:
        _print_summary(traces, base_url, verbose=args.verbose)


if __name__ == "__main__":
    main()
