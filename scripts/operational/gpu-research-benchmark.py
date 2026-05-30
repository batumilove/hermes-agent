#!/usr/bin/env python3
"""
GPU Research Pipeline — Evidence Collection & Benchmark Runner
Runs on Spark (spark-goat) and collects structured results.

Usage:
  python3 gpu-research-benchmark.py --mode collect    # Collect feed evidence + current state
  python3 gpu-research-benchmark.py --mode benchmark  # Run actual Spark benchmark
  python3 gpu-research-benchmark.py --mode full       # Both
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path.home() / ".hermes" / "gpu-research"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SPARK_URL = "http://100.69.54.37:8001"
EMBED_URL = "http://100.69.54.37:11435"

def run(cmd, timeout=30):
    """Run shell command, return output."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def spark_model_info():
    """Collect current Spark model/config info."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{SPARK_URL}/v1/models")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def spark_health():
    """Check Spark endpoint health."""
    import urllib.request
    results = {}
    for port, name in [(8001, "vllm"), (11435, "embeddings")]:
        try:
            req = urllib.request.Request(f"http://100.69.54.37:{port}/v1/models")
            start = time.time()
            with urllib.request.urlopen(req, timeout=5) as resp:
                elapsed = time.time() - start
                data = json.loads(resp.read())
                models = [m["id"] for m in data.get("data", [])]
                results[name] = {"status": "up", "latency_ms": round(elapsed * 1000), "models": models}
        except Exception as e:
            results[name] = {"status": "down", "error": str(e)}
    return results


def run_benchmark(model, prompt_tokens, max_tokens, num_requests=3):
    """Run a simple benchmark against the Spark."""
    import urllib.request
    results = []
    url = f"{SPARK_URL}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Write a detailed technical explanation of transformer attention mechanisms, covering multi-head attention, scaled dot-product, and key-value caching." * (prompt_tokens // 50)}],
        "max_tokens": max_tokens,
        "temperature": 0.1
    }).encode()

    for i in range(num_requests):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            start = time.time()
            with urllib.request.urlopen(req, timeout=120) as resp:
                elapsed = time.time() - start
                data = json.loads(resp.read())
                usage = data.get("usage", {})
                completion_tokens = usage.get("completion_tokens", 0)
                prompt_tok = usage.get("prompt_tokens", 0)
                tok_per_s = completion_tokens / elapsed if elapsed > 0 else 0
                results.append({
                    "request": i + 1,
                    "prompt_tokens": prompt_tok,
                    "completion_tokens": completion_tokens,
                    "latency_s": round(elapsed, 2),
                    "tok_per_s": round(tok_per_s, 2),
                })
        except Exception as e:
            results.append({"request": i + 1, "error": str(e)})

    return results


def collect_feed_evidence():
    """Collect latest articles from blogwatcher, extract version info."""
    evidence = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "feed_evidence",
    }

    # Get recent unread articles
    articles_raw = run("~/blogwatcher-cli articles 2>&1 | head -120")

    # Extract version-related items
    version_patterns = ["release", "v0.", "v1.", "b9", "nvfp4", "blackwell", "spark", "gb10", "sglang", "vllm", "llama.cpp"]
    relevant = []
    for line in articles_raw.split("\n"):
        line_lower = line.lower()
        if any(p in line_lower for p in version_patterns):
            relevant.append(line.strip())

    evidence["relevant_articles"] = relevant[:30]
    evidence["total_unread"] = len(articles_raw.split("[")) - 1

    # GitHub version tracking
    versions = {}
    for engine, repo in [
        ("vllm", "vllm-project/vllm"),
        ("sglang", "sgl-project/sglang"),
        ("llama_cpp", "ggml-org/llama.cpp"),
        ("tensorrt_llm", "NVIDIA/TensorRT-LLM"),
        ("ollama", "ollama/ollama"),
    ]:
        tag = run(f"curl -sL https://api.github.com/repos/{repo}/releases/latest 2>&1 | jq -r '.tag_name' 2>/dev/null")
        date = run(f"curl -sL https://api.github.com/repos/{repo}/releases/latest 2>&1 | jq -r '.published_at' 2>/dev/null")
        versions[engine] = {"version": tag, "published": date}

    evidence["latest_versions"] = versions

    # Spark current state
    evidence["spark_health"] = spark_health()
    evidence["spark_models"] = spark_model_info()

    return evidence


def save_result(data, prefix="evidence"):
    """Save structured result to file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{prefix}_{ts}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["collect", "benchmark", "full"], default="collect")
    parser.add_argument("--model", default="aeon-ultimate")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--num-requests", type=int, default=3)
    args = parser.parse_args()

    print(f"🔬 GPU Research Pipeline — {args.mode} mode")
    print(f"   Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print()

    if args.mode in ("collect", "full"):
        print("📋 Collecting feed evidence...")
        evidence = collect_feed_evidence()
        path = save_result(evidence, "evidence")
        print(f"   Saved: {path}")
        print(f"   Unread articles: {evidence['total_unread']}")
        print(f"   Relevant items: {len(evidence['relevant_articles'])}")
        print(f"   Spark health: {json.dumps(evidence['spark_health'], indent=2)}")
        print(f"   Versions: {json.dumps(evidence['latest_versions'], indent=2)}")
        print()

    if args.mode in ("benchmark", "full"):
        print(f"⚡ Running benchmark: model={args.model}, max_tokens={args.max_tokens}, requests={args.num_requests}")
        results = run_benchmark(args.model, 100, args.max_tokens, args.num_requests)
        bench_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "benchmark",
            "model": args.model,
            "max_tokens": args.max_tokens,
            "results": results,
            "avg_tok_per_s": round(sum(r.get("tok_per_s", 0) for r in results if "tok_per_s" in r) / max(1, len([r for r in results if "tok_per_s" in r])), 2),
        }
        path = save_result(bench_data, "benchmark")
        print(f"   Saved: {path}")
        print(f"   Results: {json.dumps(results, indent=2)}")
        print(f"   Average throughput: {bench_data['avg_tok_per_s']} tok/s")
        print()

    # Print summary for cron consumption
    print("✅ Pipeline complete")


if __name__ == "__main__":
    main()
