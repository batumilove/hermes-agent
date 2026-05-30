#!/usr/bin/env python3
"""Quick DFlash benchmark for DGX Spark."""
import urllib.request, json, time

SPARK_URL = "http://100.69.54.37:8001/v1/chat/completions"

benchmarks = {
    "code": "Write a binary search function in Python with type hints and docstring.",
    "reasoning": "Explain step by step: if a train travels 120km in 2 hours, then 80km in 1.5 hours, what is the average speed?",
    "extraction": 'Extract the name, age, and city from this text as JSON: "John Smith is a 34-year-old software engineer living in Austin, Texas."',
    "prose": "Write a short paragraph about the history of artificial intelligence.",
}

all_results = {}

for category, prompt in benchmarks.items():
    payload = json.dumps({
        "model": "aeon-ultimate",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 256,
        "temperature": 0
    }).encode()

    tok_per_s_list = []
    for run in range(3):
        try:
            req = urllib.request.Request(SPARK_URL, data=payload, headers={"Content-Type": "application/json"})
            start = time.time()
            with urllib.request.urlopen(req, timeout=120) as resp:
                elapsed = time.time() - start
                data = json.loads(resp.read())
            comp = data.get("usage", {}).get("completion_tokens", 0)
            tps = round(comp / elapsed, 2) if elapsed > 0 else 0
            tok_per_s_list.append(tps)
            print(f"  {category} run {run+1}: {comp} tokens in {round(elapsed,2)}s = {tps} tok/s")
        except Exception as e:
            print(f"  {category} run {run+1}: ERROR {e}")

    if tok_per_s_list:
        avg = round(sum(tok_per_s_list) / len(tok_per_s_list), 2)
        all_results[category] = avg
        print(f"  {category} AVG: {avg} tok/s\n")

if all_results:
    overall = round(sum(all_results.values()) / len(all_results), 2)
    print(f"=== OVERALL: {overall} tok/s ===")
    print(f"AEON DFlash v4 reference: 37.56 tok/s")
    print(f"Delta: {round(overall - 37.56, 2)} tok/s")
