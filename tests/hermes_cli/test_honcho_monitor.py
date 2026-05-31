"""Tests for the Honcho production smoke monitor."""

from __future__ import annotations

from pathlib import Path

from hermes_cli import honcho_monitor as hm


def test_short_host_maps_both_dgx_spark_nodes():
    assert hm.short_host("http://100.69.54.37:8001/v1") == "spark-goat"
    assert hm.short_host("http://100.71.155.95:11435/v1") == "spark-polarbear"


def test_parse_pipeline_env_extracts_loaded_embedding_config():
    raw = "\n".join(
        [
            "EMBEDDING_MODEL_CONFIG__MODEL=qwen3-embedding-8b-1536",
            "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=http://100.69.54.37:11435/v1",
            "EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE=never",
            "EMBEDDING_VECTOR_DIMENSIONS=1536",
            "DERIVER_MODEL_CONFIG__MODEL=aeon-ultimate",
            "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=http://100.69.54.37:8001/v1",
            "SUMMARY_MODEL_CONFIG__MODEL=qwen3.5-397b",
            "SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=http://100.110.104.77:8087/v1",
            "DREAM_DEDUCTION_MODEL_CONFIG__MODEL=aeon-ultimate",
            "DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://100.69.54.37:8001/v1",
            "DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL=aeon-ultimate",
            "DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL=http://100.69.54.37:8001/v1",
        ]
    )

    parsed = hm.parse_pipeline_env(raw)

    assert parsed["embedding"]["model"] == "qwen3-embedding-8b-1536"
    assert parsed["embedding"]["base_url"] == "http://100.69.54.37:11435/v1"
    assert parsed["embedding"]["dimensions_mode"] == "never"
    assert parsed["embedding"]["vector_dimensions"] == "1536"
    assert parsed["deriver"]["model"] == "aeon-ultimate"
    assert parsed["summary"]["base_url"] == "http://100.110.104.77:8087/v1"
    assert parsed["dialectic"]["base_url"] == "http://100.69.54.37:8001/v1"


def test_format_report_shows_loaded_config_dimensions_latency_and_alerts(tmp_path: Path):
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "text-embedding-3-small",
                "base_url": "https://api.openai.com/v1",
                "dimensions_mode": "",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 100,
            "documents_with_embeddings": 95,
            "documents_dims": 768,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 768,
        },
        "queue": {"pending": 20, "done": 80},
        "errors": {"save_representation": 2, "four_oh_one": 1},
        "spark_goat": {"ok": True, "latency_s": 1.25, "thinking": False},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }
    previous = {"queue_done": 70, "documents_total": 91}

    report = hm.format_report(snapshot, previous_state=previous, now=hm.datetime(2026, 5, 28, 23, 51))

    assert report.startswith("🩺 Honcho —")
    assert "Embedding: text-embedding-3-small @ openai" in report
    assert "Embedding env: model=text-embedding-3-small base_url=https://api.openai.com/v1" in report
    assert "Embedding DB: docs 95/100 dims=768 · messages 80/80 dims=768" in report
    assert "Δ15m: queue +10 · docs +9" in report
    assert "Recent errors: save-repr=2 · 401=1" in report
    assert "spark-goat chat: 1.2s" in report
    assert "⚠️" in report
    assert "Embedding config looks like OpenAI fallback" in report
    assert "Queue advancing faster than documents" in report


def test_state_round_trip(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state = {"queue_done": 80, "documents_total": 100, "messages_total": 200}

    hm.save_state(state_path, state)

    assert hm.load_state(state_path) == state
