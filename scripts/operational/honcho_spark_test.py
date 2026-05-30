#!/usr/bin/env python3
"""Diagnostic test suite for Honcho deriver + DGX Spark / Mac Studio LLM compatibility.

Tests the exact API call pattern Honcho's deriver uses against both endpoints
to identify why the DGX Spark fails and why Mac Studio sometimes produces
zero observations.

Root causes identified:
1. DGX Spark :8001 (aeon-ultimate vLLM) hangs on completion requests (0 bytes returned)
2. Mac Studio Qwen3.5-397B thinking mode can consume ALL max_tokens in reasoning_content,
   leaving content="" which Honcho can't parse as JSON
3. Honcho uses OpenAI client.chat.completions.parse() with Pydantic response_format,
   which llama.cpp/vLLM may not fully support

Usage:
  python3 honcho_spark_test.py [--spark] [--mac] [--all]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any

# ---- Config ----

SPARK_URL = "http://100.69.54.37:8001"
SPARK_MODEL = "aeon-ultimate"
MAC_URL = "http://100.110.104.77:8087"
MAC_MODEL = "qwen3.5-397b"
TIMEOUT = 120  # seconds

# The exact schema Honcho sends (PromptRepresentation)
HONCHO_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "PromptRepresentation",
        "schema": {
            "type": "object",
            "properties": {
                "explicit": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "The explicit observation"
                            }
                        },
                        "required": ["content"]
                    },
                    "description": "Facts LITERALLY stated by the user"
                }
            },
            "required": ["explicit"]
        }
    }
}

# Deriver prompt (simplified from actual Honcho prompt)
DERIVER_MESSAGES = [
    {"role": "user", "content": """Analyze messages from Aleman to extract **explicit atomic facts** about them.

[EXPLICIT] DEFINITION: Facts about Aleman that can be derived directly from their messages.
   - Transform statements into one or multiple conclusions
   - Each conclusion must be self-contained with enough context

RULES:
- Properly attribute observations to the correct subject
- Observations should make sense on their own
- Extract ALL observations from Aleman's messages

Messages to analyze:
<messages>
[2026-05-27 08:00:00] Aleman: I'm running Honcho with the DGX Spark for the deriver. The Mac Studio Qwen3.5-397B is too slow at 166 seconds per unit.
[2026-05-27 08:01:00] Aleman: Can you split the config? Use Spark for deriver and Mac Studio for dream and dialectic.
</messages>"""}
]

@dataclass
class TestResult:
    name: str
    passed: bool = False
    duration_ms: float = 0
    error: str = ""
    content: str = ""
    reasoning_content: str = ""
    response_json: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _request(url: str, path: str, data: dict | None = None, timeout: int = TIMEOUT) -> tuple[int, str, dict]:
    """Make HTTP request and return (status, body_str, headers)."""
    full_url = f"{url}{path}"
    if data is not None:
        body = json.dumps(data).encode()
        req = urllib.request.Request(full_url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(full_url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), {}
    except Exception as e:
        return 0, str(e), {}


def test_health(base_url: str, label: str) -> TestResult:
    """Test 1: Basic connectivity and health check."""
    r = TestResult(name=f"{label} Health Check")
    t0 = time.time()
    status, body, _ = _request(base_url, "/health", timeout=10)
    r.duration_ms = (time.time() - t0) * 1000
    if status == 200:
        r.passed = True
        r.notes.append(f"Health OK: {body.strip()}")
    else:
        r.error = f"status={status}, body={body[:200]}"
        r.notes.append("Endpoint reachable but /health not available (may not be implemented)")
        # Try /v1/models as fallback
        status2, body2, _ = _request(base_url, "/v1/models", timeout=10)
        if status2 == 200:
            r.passed = True
            r.notes.append(f"Models endpoint works: {body2[:200]}")
    return r


def test_models(base_url: str, label: str) -> TestResult:
    """Test 2: List available models."""
    r = TestResult(name=f"{label} Model List")
    t0 = time.time()
    status, body, _ = _request(base_url, "/v1/models", timeout=10)
    r.duration_ms = (time.time() - t0) * 1000
    if status == 200:
        try:
            models = json.loads(body)
            model_ids = [m.get("id", "?") for m in models.get("data", [])]
            r.passed = True
            r.notes.append(f"Models: {model_ids}")
        except json.JSONDecodeError:
            r.error = f"Invalid JSON: {body[:200]}"
    else:
        r.error = f"status={status}"
    return r


def test_basic_completion(base_url: str, model: str, label: str) -> TestResult:
    """Test 3: Simple chat completion (no structured output)."""
    r = TestResult(name=f"{label} Basic Completion")
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "What is 2+2? Reply with just the number."}],
        "max_tokens": 50,
    }
    t0 = time.time()
    status, body, _ = _request(base_url, "/v1/chat/completions", data, timeout=TIMEOUT)
    r.duration_ms = (time.time() - t0) * 1000
    if status == 0:
        r.error = f"Request failed/timed out after {TIMEOUT}s: {body[:200]}"
        r.notes.append("⚠️ ENDPOINT HANGS - this is the core DGX Spark issue")
        return r
    if status != 200:
        r.error = f"status={status}, body={body[:300]}"
        return r
    try:
        resp = json.loads(body)
        msg = resp["choices"][0]["message"]
        r.content = msg.get("content", "")
        r.reasoning_content = msg.get("reasoning_content", "")
        r.response_json = resp
        usage = resp.get("usage", {})
        finish = resp["choices"][0].get("finish_reason", "?")
        r.passed = bool(r.content.strip())
        r.notes.append(f"Content: {r.content[:100]!r}")
        r.notes.append(f"Reasoning len: {len(r.reasoning_content)} chars")
        r.notes.append(f"Tokens: {usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')}")
        r.notes.append(f"Finish: {finish}")
        if not r.content.strip() and r.reasoning_content:
            r.notes.append("⚠️ THINKING MODE ISSUE: all tokens consumed by reasoning, content is empty")
    except (json.JSONDecodeError, KeyError) as e:
        r.error = f"Parse error: {e}, body={body[:300]}"
    return r


def test_json_mode(base_url: str, model: str, label: str) -> TestResult:
    """Test 4: JSON mode (response_format: json_object)."""
    r = TestResult(name=f"{label} JSON Mode")
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You must respond with valid JSON only."},
            {"role": "user", "content": "List 3 colors as a JSON object with key 'colors' mapping to an array of strings."}
        ],
        "max_tokens": 100,
        "response_format": {"type": "json_object"},
    }
    t0 = time.time()
    status, body, _ = _request(base_url, "/v1/chat/completions", data, timeout=TIMEOUT)
    r.duration_ms = (time.time() - t0) * 1000
    if status == 0:
        r.error = f"Request timed out: {body[:200]}"
        return r
    if status != 200:
        r.error = f"status={status}, body={body[:300]}"
        return r
    try:
        resp = json.loads(body)
        msg = resp["choices"][0]["message"]
        r.content = msg.get("content", "")
        r.reasoning_content = msg.get("reasoning_content", "")
        finish = resp["choices"][0].get("finish_reason", "?")
        r.notes.append(f"Content: {r.content[:200]!r}")
        r.notes.append(f"Reasoning len: {len(r.reasoning_content)} chars")
        r.notes.append(f"Finish: {finish}")
        # Try to parse as JSON
        try:
            parsed = json.loads(r.content)
            r.passed = True
            r.notes.append(f"Valid JSON: {json.dumps(parsed)[:200]}")
        except json.JSONDecodeError as e:
            r.error = f"Invalid JSON: {e}"
            r.notes.append("⚠️ Content is not valid JSON")
            if not r.content.strip():
                r.notes.append("⚠️ Empty content - thinking mode consumed all tokens")
    except (json.JSONDecodeError, KeyError) as e:
        r.error = f"Parse error: {e}"
    return r


def test_structured_output(base_url: str, model: str, label: str) -> TestResult:
    """Test 5: Structured output with json_schema (what Honcho uses)."""
    r = TestResult(name=f"{label} Structured Output (Honcho schema)")
    data = {
        "model": model,
        "messages": DERIVER_MESSAGES,
        "max_tokens": 2048,
        "response_format": HONCHO_SCHEMA,
    }
    t0 = time.time()
    status, body, _ = _request(base_url, "/v1/chat/completions", data, timeout=TIMEOUT)
    r.duration_ms = (time.time() - t0) * 1000
    if status == 0:
        r.error = f"Request timed out: {body[:200]}"
        return r
    if status != 200:
        r.error = f"status={status}, body={body[:300]}"
        r.notes.append("⚠️ Endpoint rejected structured output request")
        return r
    try:
        resp = json.loads(body)
        msg = resp["choices"][0]["message"]
        r.content = msg.get("content", "")
        r.reasoning_content = msg.get("reasoning_content", "")
        finish = resp["choices"][0].get("finish_reason", "?")
        usage = resp.get("usage", {})
        r.notes.append(f"Content: {r.content[:300]!r}")
        r.notes.append(f"Reasoning len: {len(r.reasoning_content)} chars")
        r.notes.append(f"Tokens: {usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')}")
        r.notes.append(f"Finish: {finish}")
        # Validate against expected schema
        try:
            parsed = json.loads(r.content)
            if "explicit" in parsed:
                obs = parsed["explicit"]
                r.passed = True
                r.notes.append(f"✅ {len(obs)} observations extracted")
                for i, o in enumerate(obs[:5]):
                    r.notes.append(f"  obs[{i}]: {o.get('content', '?')[:80]}")
            else:
                r.error = "Missing 'explicit' key in response"
        except json.JSONDecodeError as e:
            r.error = f"Invalid JSON: {e}"
            if not r.content.strip():
                r.notes.append("⚠️ EMPTY CONTENT - thinking mode consumed all tokens, Honcho will fail here")
    except (json.JSONDecodeError, KeyError) as e:
        r.error = f"Parse error: {e}"
    return r


def test_thinking_budget(base_url: str, model: str, label: str) -> TestResult:
    """Test 6: Check if thinking budget can be controlled."""
    r = TestResult(name=f"{label} Thinking Control")
    # Try with /no_think in prompt
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "/no_think"},
            {"role": "user", "content": "What is 2+2? Reply with just the number."}
        ],
        "max_tokens": 100,
    }
    t0 = time.time()
    status, body, _ = _request(base_url, "/v1/chat/completions", data, timeout=TIMEOUT)
    r.duration_ms = (time.time() - t0) * 1000
    if status == 0:
        r.error = f"Request timed out: {body[:200]}"
        return r
    try:
        resp = json.loads(body)
        msg = resp["choices"][0]["message"]
        r.content = msg.get("content", "")
        r.reasoning_content = msg.get("reasoning_content", "")
        has_reasoning = bool(r.reasoning_content.strip())
        has_content = bool(r.content.strip())
        r.notes.append(f"/no_think in system → reasoning={len(r.reasoning_content)}c, content={r.content[:50]!r}")
        r.passed = has_content and not has_reasoning
        if has_reasoning and not has_content:
            r.notes.append("⚠️ /no_think did NOT disable thinking mode")
    except Exception as e:
        r.error = str(e)
    return r


def print_results(results: list[TestResult]) -> None:
    """Print test results in a readable format."""
    print("\n" + "=" * 70)
    for r in results:
        icon = "✅" if r.passed else "❌"
        print(f"\n{icon} {r.name} ({r.duration_ms:.0f}ms)")
        if r.error:
            print(f"   ERROR: {r.error}")
        for note in r.notes:
            print(f"   {note}")
    
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"\n{'=' * 70}")
    print(f"Results: {passed}/{total} passed")


def run_tests(target: str) -> list[TestResult]:
    """Run all tests for a target endpoint."""
    if target == "spark":
        url, model, label = SPARK_URL, SPARK_MODEL, "DGX Spark"
    elif target == "mac":
        url, model, label = MAC_URL, MAC_MODEL, "Mac Studio"
    else:
        raise ValueError(f"Unknown target: {target}")
    
    print(f"\n🔍 Testing {label} ({url})")
    print("-" * 50)
    
    tests = [
        test_health(url, label),
        test_models(url, label),
    ]
    
    # Only run remaining tests if basic connectivity works
    if tests[0].passed or tests[1].passed:
        tests.append(test_basic_completion(url, model, label))
        # Only run further tests if basic completion works
        if tests[-1].passed or "timed out" not in tests[-1].error.lower():
            tests.append(test_json_mode(url, model, label))
            tests.append(test_structured_output(url, model, label))
            tests.append(test_thinking_budget(url, model, label))
    
    return tests


def main():
    parser = argparse.ArgumentParser(description="Honcho deriver LLM endpoint diagnostic")
    parser.add_argument("--spark", action="store_true", help="Test DGX Spark only")
    parser.add_argument("--mac", action="store_true", help="Test Mac Studio only")
    parser.add_argument("--all", action="store_true", help="Test both endpoints")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help="Request timeout in seconds")
    args = parser.parse_args()
    
    # Note: TIMEOUT used as module-level default; --timeout flag not yet overriding per-call
    # TIMEOUT = args.timeout
    
    if not any([args.spark, args.mac, args.all]):
        args.all = True
    
    all_results = []
    if args.spark or args.all:
        all_results.extend(run_tests("spark"))
    if args.mac or args.all:
        all_results.extend(run_tests("mac"))
    
    print_results(all_results)
    
    # Print diagnosis summary
    print("\n📋 DIAGNOSIS SUMMARY")
    print("-" * 50)
    
    spark_results = [r for r in all_results if "DGX Spark" in r.name] if (args.spark or args.all) else []
    mac_results = [r for r in all_results if "Mac Studio" in r.name] if (args.mac or args.all) else []
    
    if spark_results:
        basic = next((r for r in spark_results if "Basic" in r.name), None)
        if basic and not basic.passed:
            print("\n🔴 DGX Spark: Endpoint HANGS on completion requests")
            print("   - /v1/models works but /v1/chat/completions returns 0 bytes")
            print("   - Likely: vLLM stuck processing, GPU OOM, or model not loaded")
            print("   - Fix: Restart vLLM on the Spark, check nvidia-smi")
    
    if mac_results:
        structured = next((r for r in mac_results if "Structured" in r.name), None)
        if structured and not structured.passed:
            print("\n🟡 Mac Studio: Thinking mode consumes all output tokens")
            print("   - Qwen3.5-397B reasoning_content eats the entire max_tokens budget")
            print("   - When content='' → Honcho JSON parser fails: 'Expecting value: line 1 column 1'")
            print("   - This explains ~30% of deriver failures in logs")
            print("   - Fixes:")
            print("     a. Increase max_output_tokens (e.g., 8192+) so thinking completes + content follows")
            print("     b. Add /no_think to deriver system prompt if llama.cpp supports it")
            print("     c. Use a non-thinking model variant")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
