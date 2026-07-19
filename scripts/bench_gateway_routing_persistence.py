#!/usr/bin/env python3
"""Disposable benchmark: gateway routing persistence on a ~2.3k-row scope.

Compares the atomic full-scope reconciliation path against the point-update
routine path used by SessionStore._persist_routing_data. Reports p50/p95/max
latencies for each strategy.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from hermes_state import SessionDB


def seed_rows(db: SessionDB, n: int = 2297, scope: str = "gateway") -> dict[str, str]:
    rows = {f"k{i:04d}": json.dumps({"session_id": f"s{i:04d}"}) for i in range(n)}
    db.replace_gateway_routing_entries(rows, scope=scope)
    return rows


def bench_full_replace(db: SessionDB, rows: dict[str, str], scope: str = "gateway") -> float:
    updated = dict(rows)
    updated["k0001"] = json.dumps({"session_id": "changed"})
    start = time.perf_counter()
    db.replace_gateway_routing_entries(updated, scope=scope)
    return time.perf_counter() - start


def bench_point_update(db: SessionDB, rows: dict[str, str], scope: str = "gateway") -> float:
    start = time.perf_counter()
    db.save_gateway_routing_entry("k0001", json.dumps({"session_id": "changed"}), scope=scope)
    return time.perf_counter() - start


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(name: str, values: list[float]) -> dict:
    return {
        "strategy": name,
        "samples": len(values),
        "p50_seconds": round(percentile(values, 0.50), 6),
        "p95_seconds": round(percentile(values, 0.95), 6),
        "max_seconds": round(max(values), 6),
    }


def main() -> None:
    iterations = 50
    with tempfile.TemporaryDirectory() as td:
        db = SessionDB(db_path=Path(td) / "state.db")
        try:
            rows = seed_rows(db)
            full_times = [bench_full_replace(db, rows) for _ in range(iterations)]
            point_times = [bench_point_update(db, rows) for _ in range(iterations)]
            result = {
                "rows": len(rows),
                "scope": "gateway",
                "full_replace": summarize("full_replace", full_times),
                "point_update": summarize("point_update", point_times),
                "speedup_p50": round(
                    summarize("full_replace", full_times)["p50_seconds"]
                    / summarize("point_update", point_times)["p50_seconds"],
                    2,
                ) if point_times else None,
            }
            print(json.dumps(result, indent=2))
        finally:
            db.close()


if __name__ == "__main__":
    main()
