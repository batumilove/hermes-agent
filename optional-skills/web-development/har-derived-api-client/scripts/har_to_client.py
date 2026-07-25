#!/usr/bin/env python3
"""Distill a HAR file into an API summary an agent can turn into a client.

Usage:
  python3 har_to_client.py <input.har> [--include-static] [--host SUBSTRING] [--max-body 600]

Filters to XHR/fetch/JSON traffic by default, groups by (method, host, path
template), and prints per-endpoint: query params, interesting request headers,
request body sample, response content-type/status, and a response body sample.
Numeric/UUID-ish path segments are collapsed to {id} so repeated calls group.
Also prints "### Replay hints": the browser User-Agent plus whether cookies or
auth/token headers were present -- send those in the derived client or you may
get a 403/401.
"""
import argparse
import json
import re
import sys
from collections import OrderedDict
from urllib.parse import parse_qsl, urlsplit

BORING_HEADERS = {
    "accept-encoding", "accept-language", "connection", "content-length",
    "host", "origin", "referer", "sec-ch-ua", "sec-ch-ua-mobile",
    "sec-ch-ua-platform", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "user-agent", "pragma", "cache-control", "priority", "te",
    "upgrade-insecure-requests", "cookie",
}
ID_SEG = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{16,})$", re.I)
STATIC_EXT = re.compile(r"\.(js|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|mp4|map)$", re.I)
SENSITIVE_MARKERS = {
    "auth", "authorization", "credential", "cookie", "key", "password",
    "secret", "session", "signature", "token",
}


def is_sensitive_name(name: str) -> bool:
    """Recognize secret-bearing header/JSON keys, including camelCase names."""
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return bool(SENSITIVE_MARKERS.intersection(re.findall(r"[a-z0-9]+", snake)))


def redact_body(text: str, mime: str) -> str:
    """Mask values of secret-shaped JSON keys while preserving payload shape."""
    if not isinstance(text, str) or not text or "json" not in mime.lower():
        return text
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text

    def walk(value):
        if isinstance(value, dict):
            return {key: ("<redacted>" if is_sensitive_name(str(key)) else walk(item))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return json.dumps(walk(payload), ensure_ascii=False)


def path_template(path: str) -> str:
    segs = path.split("/")
    return "/".join("{id}" if ID_SEG.match(s) else s for s in segs)


def is_api_entry(entry: dict) -> bool:
    req = entry["request"]
    resp = entry.get("response", {})
    rtype = (entry.get("_resourceType") or "").lower()
    mime = (resp.get("content", {}).get("mimeType") or "").lower()
    if rtype in ("xhr", "fetch"):
        return True
    if "json" in mime:
        return True
    if req["method"] not in ("GET", "HEAD") and not STATIC_EXT.search(urlsplit(req["url"]).path):
        return True
    return False


def trunc(text, n: int) -> str:
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= n else text[:n] + f"... [{len(text)} chars total]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("har")
    ap.add_argument("--include-static", action="store_true")
    ap.add_argument("--host", default=None, help="only endpoints whose host contains this")
    ap.add_argument("--max-body", type=int, default=600)
    args = ap.parse_args()

    try:
        with open(args.har, encoding="utf-8") as f:
            har = json.load(f)
        entries = har["log"]["entries"]
        if not isinstance(entries, list):
            raise TypeError("log.entries is not a list")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"Not a valid HAR file: {exc}", file=sys.stderr)
        return 2

    groups = OrderedDict()
    for entry in entries:
        req = entry["request"]
        url = urlsplit(req["url"])
        if url.scheme not in ("http", "https"):
            continue
        if args.host and args.host not in url.netloc:
            continue
        if not args.include_static:
            if STATIC_EXT.search(url.path) or not is_api_entry(entry):
                continue
        key = (req["method"], url.netloc, path_template(url.path))
        g = groups.setdefault(key, {"count": 0, "queries": set(), "headers": {},
                                    "req_body": None, "resp": None})
        g["count"] += 1
        query_items = req.get("queryString") or [
            {"name": name, "value": value}
            for name, value in parse_qsl(url.query, keep_blank_values=True)
        ]
        for q in query_items:
            value = "<redacted>" if is_sensitive_name(q["name"]) else trunc(q["value"], 80)
            g["queries"].add((q["name"], value))
        for h in req.get("headers", []):
            name = h["name"].lower().lstrip(":")
            if name in BORING_HEADERS or name in ("method", "path", "scheme", "authority"):
                continue
            g["headers"][name] = "<redacted>" if is_sensitive_name(name) else trunc(h["value"], 120)
        post = req.get("postData", {})
        if post.get("text") and g["req_body"] is None:
            mime = post.get("mimeType", "")
            g["req_body"] = (mime, trunc(redact_body(post["text"], mime), args.max_body))
        resp = entry.get("response", {})
        if g["resp"] is None and resp:
            content = resp.get("content", {})
            mime = content.get("mimeType", "")
            g["resp"] = (resp.get("status"), mime,
                         trunc(redact_body(content.get("text") or "", mime), args.max_body))

    if not groups:
        print("No API-looking entries found. Re-run with --include-static to see everything.")
        return 1

    # Surface the browser identity so the replay client can match it (many
    # sites 403 a default library User-Agent).
    ua = None
    saw_cookie = saw_auth = False
    for entry in entries:
        for h in entry["request"].get("headers", []):
            n = h["name"].lower()
            if n == "user-agent" and ua is None:
                ua = h["value"]
            if n == "cookie":
                saw_cookie = True
            if n in ("authorization", "x-api-key") or "token" in n:
                saw_auth = True
    print("### Replay hints")
    if ua:
        print(f"  User-Agent (send this): {ua}")
    if saw_cookie:
        print("  Cookies present -> session may be auth-gated; capture & resend the Cookie header.")
    if saw_auth:
        print("  Authorization/token header present -> extract and resend it.")

    for (method, host, path), g in groups.items():
        print(f"\n=== {method} https://{host}{path}  (x{g['count']})")
        if g["queries"]:
            print("  query params:")
            for name, val in sorted(g["queries"]):
                print(f"    {name} = {val}")
        if g["headers"]:
            print("  request headers (non-boring):")
            for name, val in sorted(g["headers"].items()):
                print(f"    {name}: {val}")
        if g["req_body"]:
            print(f"  request body ({g['req_body'][0]}):")
            print(f"    {g['req_body'][1]}")
        if g["resp"]:
            status, mime, body = g["resp"]
            print(f"  response: {status} {mime}")
            if body:
                print(f"    {body}")
    print(f"\n{len(groups)} distinct endpoints.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
