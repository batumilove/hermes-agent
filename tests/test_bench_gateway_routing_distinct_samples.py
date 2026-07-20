"""Regression coverage for the gateway routing persistence benchmark.

CodeRabbit finding: the benchmark must write a distinct value on every sample so
it cannot silently regress to no-op updates.
"""

import json
import tempfile
from pathlib import Path

from hermes_state import SessionDB

from scripts.bench_gateway_routing_persistence import (
    bench_full_replace,
    bench_point_update,
    seed_rows,
)


def test_bench_full_replace_writes_distinct_values_per_iteration():
    with tempfile.TemporaryDirectory() as td:
        db = SessionDB(db_path=Path(td) / "state.db")
        try:
            rows = seed_rows(db)
            values = set()
            for i in range(10):
                bench_full_replace(db, rows, i)
                row = db.load_gateway_routing_entries(scope="gateway")["k0001"]
                values.add(json.loads(row)["session_id"])
            assert len(values) == 10, values
        finally:
            db.close()


def test_bench_point_update_writes_distinct_values_per_iteration():
    with tempfile.TemporaryDirectory() as td:
        db = SessionDB(db_path=Path(td) / "state.db")
        try:
            rows = seed_rows(db)
            values = set()
            for i in range(10):
                bench_point_update(db, rows, i)
                row = db.load_gateway_routing_entries(scope="gateway")["k0001"]
                values.add(json.loads(row)["session_id"])
            assert len(values) == 10, values
        finally:
            db.close()


def test_bench_full_and_point_values_are_distinguishable_sets():
    """Full-replace and point-update iteration ranges must not overlap so a
    combined run cannot accidentally reuse a value."""
    with tempfile.TemporaryDirectory() as td:
        db = SessionDB(db_path=Path(td) / "state.db")
        try:
            rows = seed_rows(db)
            full_values = set()
            for i in range(5):
                bench_full_replace(db, rows, i)
                row = db.load_gateway_routing_entries(scope="gateway")["k0001"]
                full_values.add(json.loads(row)["session_id"])
            point_values = set()
            for i in range(5):
                bench_point_update(db, rows, i + 1000)
                row = db.load_gateway_routing_entries(scope="gateway")["k0001"]
                point_values.add(json.loads(row)["session_id"])
            assert full_values.isdisjoint(point_values), (full_values, point_values)
        finally:
            db.close()
