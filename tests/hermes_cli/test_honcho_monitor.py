"""Tests for the Honcho production smoke monitor."""

from __future__ import annotations

from pathlib import Path

from hermes_cli import honcho_monitor as hm


def test_short_host_maps_both_dgx_spark_nodes():
    assert hm.short_host("http://192.168.10.211:8001/v1") == "spark-goat"
    assert hm.short_host("http://100.69.54.37:8001/v1") == "spark-goat"
    assert hm.short_host("http://100.71.155.95:11435/v1") == "spark-polarbear"
    assert hm.short_host("http://100.71.155.95:18081/v1") == "spark-polarbear"


def test_honcho_target_defaults_to_lan_ssh_to_avoid_tailscale_approval_gate():
    assert hm.HONCHO_TARGET == "ubuntu@honcho.teleport.batumi.works"


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
            "documents_total": 94,
            "documents_with_embeddings": 89,
            "documents_dims": 768,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 768,
        },
        "queue": {"pending": 20, "done": 80},
        "queue_by_type": {"representation": {"pending": 15, "done": 75}, "reconciler": {"pending": 5, "done": 5}},
        "errors": {"save_representation": 2, "four_oh_one": 1},
        "spark_goat": {"ok": True, "latency_s": 1.25, "thinking": False},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }
    previous = {"queue_done": 70, "documents_total": 91, "queue_by_type": {"representation": {"pending": 10, "done": 70}}}

    report = hm.format_report(snapshot, previous_state=previous, now=hm.datetime(2026, 5, 28, 23, 51))

    assert report.startswith("🩺 Honcho —")
    assert "Embedding: text-embedding-3-small @ openai" in report
    assert "Embedding env: model=text-embedding-3-small base_url=https://api.openai.com/v1" in report
    assert "Embedding DB: docs 89/94 dims=768 · messages 80/80 dims=768" in report
    assert "Δ15m: representation +5 · reconciler +5 · webhook +0 · dream +0 · docs +3" in report
    assert "Recent errors: save-repr=2 · 401=1" in report
    assert "spark-goat chat: 1.2s" in report
    assert "⚠️" in report
    assert "Embedding config looks like OpenAI fallback" in report
    assert "Representation queue advancing faster than documents" in report


def test_format_report_infers_vector_dimensions_from_matching_db_dims():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        "db": {
            "documents_total": 88520,
            "documents_with_embeddings": 88520,
            "documents_dims": 1536,
            "messages_total": 13273,
            "messages_with_embeddings": 13270,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 153},
        "queue_by_type": {"representation": {"pending": 0, "done": 8}, "reconciler": {"pending": 0, "done": 137}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.1, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 1, "last_duration_s": 2, "conclusions": 0},
    }

    report = hm.format_report(snapshot, now=hm.datetime(2026, 7, 8, 14, 3))

    assert "vector_dims=1536 (inferred from DB)" in report


def test_state_round_trip(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state = {"queue_done": 80, "documents_total": 100, "messages_total": 200}

    hm.save_state(state_path, state)

    assert hm.load_state(state_path) == state


def test_ssh_uses_accept_new_instead_of_disabling_host_key_checks(monkeypatch):
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(hm.subprocess, "run", fake_run)
    monkeypatch.setattr(hm, "HONCHO_TARGET", "honcho.example")

    assert hm.ssh("true") == "ok"

    ssh_args = calls[0]
    assert "StrictHostKeyChecking=accept-new" in ssh_args
    assert "StrictHostKeyChecking=no" not in ssh_args


def test_ssh_uses_tbot_config_when_available(monkeypatch):
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(hm.subprocess, "run", fake_run)
    monkeypatch.setattr(hm, "HONCHO_TARGET", "honcho.example")
    monkeypatch.setattr(hm.Path, "exists", lambda self: str(self) == "/var/lib/tbot/hermes-cron-ssh/ssh_config")

    assert hm.ssh("true") == "ok"

    ssh_args = calls[0]
    assert ssh_args[:3] == ["ssh", "-F", "/var/lib/tbot/hermes-cron-ssh/ssh_config"]


def test_queue_parse_handles_postgresql_boolean_text():
    # Reproduce the live failure: PostgreSQL returns 'true'/'false' text
    queue_raw = "false|15\ntrue|128\n"
    parsed = hm.parse_queue_raw(queue_raw)
    assert parsed == {"pending": 15, "done": 128}


def test_queue_parse_falls_back_to_f_t_letters():
    queue_raw = "f|12\nt|96\n"
    parsed = hm.parse_queue_raw(queue_raw)
    assert parsed == {"pending": 12, "done": 96}


def test_queue_parse_totals_from_three_column_per_type_rows():
    queue_raw = (
        "dream|false|4\n"
        "dream|true|4\n"
        "reconciler|false|10\n"
        "reconciler|true|115\n"
        "representation|false|5\n"
        "representation|true|6\n"
        "webhook|false|3\n"
        "webhook|true|5\n"
    )
    parsed = hm.parse_queue_raw(queue_raw)
    assert parsed == {"pending": 22, "done": 130}


def test_spark_goat_failure_is_blocking_alert():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 100,
            "documents_with_embeddings": 95,
            "documents_dims": 1536,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 128},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": False, "latency_s": 0.1, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    report = hm.format_report(snapshot, now=hm.datetime(2026, 5, 28, 23, 51))

    assert "📋 Queue: 0 pending · 128 done" in report
    assert "🔴 spark-goat chat: 0.1s" in report
    assert "spark-goat chat failed" in report
    assert "✅ All nominal" not in report


def test_missing_vector_dimensions_raises_alert_when_db_dims_are_unavailable():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "",
            },
            "deriver": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 100,
            "documents_with_embeddings": 95,
            "documents_dims": 0,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 0,
        },
        "queue": {"pending": 0, "done": 128},
        "queue_by_type": {"representation": {"pending": 0, "done": 128}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot))

    assert "Embedding vector dimensions missing from env" in alerts


def test_missing_vector_dimensions_is_not_alert_when_db_dims_match():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "",
            },
            "deriver": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 100,
            "documents_with_embeddings": 95,
            "documents_dims": 1536,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 128},
        "queue_by_type": {"representation": {"pending": 0, "done": 128}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot))

    assert "Embedding vector dimensions missing from env" not in alerts


def test_parse_queue_by_type_aggregates_per_task_type_counts():
    raw = (
        "dream|false|4\n"
        "dream|true|4\n"
        "reconciler|false|10\n"
        "reconciler|true|115\n"
        "representation|false|5\n"
        "representation|true|6\n"
        "webhook|false|3\n"
        "webhook|true|5\n"
    )
    parsed = hm.parse_queue_by_type_raw(raw)
    assert parsed["dream"] == {"pending": 4, "done": 4}
    assert parsed["reconciler"] == {"pending": 10, "done": 115}
    assert parsed["representation"] == {"pending": 5, "done": 6}
    assert parsed["webhook"] == {"pending": 3, "done": 5}


def test_reconciler_churn_does_not_trigger_queue_doc_drift_alert():
    """Live-shaped case: most queue churn is reconciler, not representation."""
    prev = {
        "queue_done": 80,
        "documents_total": 91,
        "queue_by_type": {
            "representation": {"pending": 5, "done": 75},
            "reconciler": {"pending": 0, "done": 5},
        },
    }
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 97,
            "documents_with_embeddings": 95,
            "documents_dims": 1536,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 210},
        "queue_by_type": {
            "representation": {"pending": 0, "done": 81},
            "reconciler": {"pending": 0, "done": 120},
            "dream": {"pending": 0, "done": 4},
            "webhook": {"pending": 0, "done": 5},
        },
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    report = hm.format_report(snapshot, previous_state=prev, now=hm.datetime(2026, 5, 28, 23, 51))

    assert "Δ15m: representation +6 · reconciler +115 · webhook +5 · dream +4 · docs +6" in report
    assert "Representation queue advancing faster than documents" not in report


def test_representation_queue_outpacing_docs_triggers_drift_alert():
    """Representation work should still be correlated with document growth."""
    prev = {
        "queue_done": 80,
        "documents_total": 91,
        "queue_by_type": {"representation": {"pending": 0, "done": 75}},
    }
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 92,
            "documents_with_embeddings": 92,
            "documents_dims": 1536,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 90},
        "queue_by_type": {"representation": {"pending": 0, "done": 85}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot), previous_state=prev)

    assert "Representation queue advancing faster than documents" in alerts


def test_representation_backlog_with_no_progress_triggers_deriver_stall_alert():
    prev = {
        "documents_total": 91594,
        "queue_by_type": {"representation": {"pending": 7050, "done": 373}},
    }
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        "db": {
            "documents_total": 91594,
            "documents_with_embeddings": 91594,
            "documents_dims": 1536,
            "messages_total": 22130,
            "messages_with_embeddings": 22130,
            "messages_dims": 1536,
        },
        "queue": {"pending": 7365, "done": 658},
        "queue_by_type": {"representation": {"pending": 7052, "done": 373}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 0, "last_duration_s": 0, "conclusions": 0},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot), previous_state=prev)

    assert "Deriver stalled: representation backlog with no progress" in alerts


def test_ssh_transport_failure_reports_probe_failure_not_fake_service_outage():
    services = hm._parse_service_status("__SSH_ERROR__ rc=255 stderr=connection timed out")
    snapshot = {
        "services": services,
        "pipeline": {
            "embedding": {"model": "", "base_url": "", "dimensions_mode": "", "vector_dimensions": ""},
            "deriver": {"model": "", "base_url": ""},
            "summary": {"model": "", "base_url": ""},
            "dream": {"model": "", "base_url": ""},
            "dialectic": {"model": "", "base_url": ""},
        },
        "db": {"documents_total": 0, "documents_with_embeddings": 0, "documents_dims": 0, "messages_total": 0, "messages_with_embeddings": 0, "messages_dims": 0},
        "queue": {"pending": 0, "done": 0},
        "queue_by_type": {},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 0, "last_duration_s": 0, "conclusions": 0},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot), previous_state={})

    assert "Honcho SSH probe failed" in alerts
    assert "API down" not in alerts
    assert "DB down" not in alerts
    assert "Redis down" not in alerts
    assert "Deriver down" not in alerts


def test_db_stats_probe_failure_does_not_fake_zero_embedding_dims():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        "db": {
            "probe_ok": False,
            "probe_error": "ERROR: function array_length(vector, integer) does not exist",
            "documents_total": 0,
            "documents_with_embeddings": 0,
            "documents_dims": 0,
            "messages_total": 0,
            "messages_with_embeddings": 0,
            "messages_dims": 0,
        },
        "queue": {"pending": 0, "done": 128},
        "queue_by_type": {"representation": {"pending": 0, "done": 128}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot), previous_state={})

    assert alerts == ["DB stats probe failed"]


def test_stale_active_deriver_work_triggers_alert():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        "db": {
            "documents_total": 96179,
            "documents_with_embeddings": 96179,
            "documents_dims": 1536,
            "messages_total": 22153,
            "messages_with_embeddings": 22153,
            "messages_dims": 1536,
        },
        "queue": {"pending": 6522, "done": 1565},
        "queue_by_type": {"representation": {"pending": 6247, "done": 1201}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 0, "last_duration_s": 0, "conclusions": 0, "active_count": 3, "active_oldest_age_s": 901},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot), previous_state={})

    assert "Deriver active work stale (3 active, oldest 15m)" in alerts

def test_legacy_state_without_queue_by_type_does_not_crash_or_false_alert():
    """Old state files lack queue_by_type; drift alert should be skipped."""
    prev = {"queue_done": 80, "documents_total": 91}
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "summary": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
        },
        "db": {
            "documents_total": 92,
            "documents_with_embeddings": 92,
            "documents_dims": 1536,
            "messages_total": 80,
            "messages_with_embeddings": 80,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 210},
        "queue_by_type": {
            "representation": {"pending": 0, "done": 81},
            "reconciler": {"pending": 0, "done": 120},
        },
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.2, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 6, "last_duration_s": 39, "conclusions": 2602},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot), previous_state=prev)

    assert "Representation queue advancing faster than documents" not in alerts


def test_spark_model_selection_falls_back_when_deriver_routed_elsewhere():
    # When deriver points to mac-studio, the spark-goat chat smoke should not
    # use that model; it should pick aeon-ultimate known to be on spark-goat.
    pipeline = {
        "deriver": {"model": "qwen3.5-397b", "base_url": "http://100.110.104.77:8087/v1"},
    }
    assert hm.select_spark_model(pipeline) == "aeon-ultimate"


def test_spark_model_selection_uses_deriver_when_on_spark_goat():
    pipeline = {
        "deriver": {"model": "custom-model", "base_url": "http://100.69.54.37:8001/v1"},
    }
    assert hm.select_spark_model(pipeline) == "custom-model"


def test_auth_error_log_pattern_ignores_ports_and_timestamp_milliseconds():
    benign = "\n".join(
        [
            "2026-07-08 10:16:25,401 - src.deriver.queue_manager - DEBUG - Claimed 1 work units",
            '      INFO   172.18.0.1:40146 - "POST /v3/workspaces HTTP/1.1" 201',
        ]
    )
    assert hm.re.search(hm.AUTH_ERROR_LOG_PATTERN, benign) is None

    real = "Error code: 401 - invalid_api_key"
    assert hm.re.search(hm.AUTH_ERROR_LOG_PATTERN, real)


def test_build_alerts_detects_hermes_to_honcho_ingestion_staleness():
    snapshot = {
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        "db": {
            "documents_total": 88520,
            "documents_with_embeddings": 88520,
            "documents_dims": 1536,
            "messages_total": 13273,
            "messages_with_embeddings": 13270,
            "messages_dims": 1536,
        },
        "queue": {"pending": 0, "done": 123},
        "queue_by_type": {"representation": {"pending": 0, "done": 8}},
        "errors": {"save_representation": 0, "four_oh_one": 0},
        "spark_goat": {"ok": True, "latency_s": 1.3, "thinking": False, "model": "aeon-ultimate"},
        "deriver": {"runs_15m": 1, "last_duration_s": 5.0, "conclusions": 0},
        "ingestion": {"source_fresh": True, "downstream_fresh": False, "drift_s": 7200, "source_age_s": 60, "downstream_age_s": 7260},
    }

    alerts = hm.build_alerts(hm.HonchoSnapshot(**snapshot))

    assert "Hermes→Honcho ingestion stale (2.0h drift)" in alerts


def test_should_emit_report_is_false_for_nominal_snapshot(monkeypatch):
    snapshot = hm.HonchoSnapshot(
        services={"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        pipeline={
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        db={
            "documents_total": 88520,
            "documents_with_embeddings": 88520,
            "documents_dims": 1536,
            "messages_total": 13273,
            "messages_with_embeddings": 13270,
            "messages_dims": 1536,
        },
        queue={"pending": 0, "done": 123},
        queue_by_type={"representation": {"pending": 0, "done": 8}, "reconciler": {"pending": 0, "done": 111}},
        errors={"save_representation": 0, "four_oh_one": 0},
        spark_goat={"ok": True, "latency_s": 1.3, "thinking": False, "model": "aeon-ultimate"},
        deriver={"runs_15m": 1, "last_duration_s": 5.0, "conclusions": 0},
    )
    previous = {
        "queue_done": 117,
        "documents_total": 88520,
        "queue_by_type": {"representation": {"pending": 0, "done": 8}, "reconciler": {"pending": 0, "done": 105}},
    }

    monkeypatch.delenv("HONCHO_MONITOR_ALWAYS_PRINT", raising=False)

    assert hm.build_alerts(snapshot, previous_state=previous) == []
    assert hm.should_emit_report(snapshot, previous_state=previous) is False


def test_should_emit_report_can_be_forced_for_manual_debug(monkeypatch):
    snapshot = hm.HonchoSnapshot(
        services={"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        pipeline={
            "embedding": {
                "model": "qwen3-embedding-8b-1536",
                "base_url": "http://100.69.54.37:11435/v1",
                "dimensions_mode": "never",
                "vector_dimensions": "1536",
            },
            "deriver": {"model": "gemma12b-polar-gpustack", "base_url": "http://100.71.155.95:18081/v1"},
            "summary": {"model": "mlx-community--Qwen3.5-4B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
            "dream": {"model": "aeon-ultimate", "base_url": "http://100.69.54.37:8001/v1"},
            "dialectic": {"model": "mlx-community--Qwen3.5-9B-4bit", "base_url": "http://192.168.10.104:8000/v1"},
        },
        db={
            "documents_total": 88520,
            "documents_with_embeddings": 88520,
            "documents_dims": 1536,
            "messages_total": 13273,
            "messages_with_embeddings": 13270,
            "messages_dims": 1536,
        },
        queue={"pending": 0, "done": 123},
        queue_by_type={"representation": {"pending": 0, "done": 8}},
        errors={"save_representation": 0, "four_oh_one": 0},
        spark_goat={"ok": True, "latency_s": 1.3, "thinking": False, "model": "aeon-ultimate"},
        deriver={"runs_15m": 1, "last_duration_s": 5.0, "conclusions": 0},
    )
    monkeypatch.setenv("HONCHO_MONITOR_ALWAYS_PRINT", "1")

    assert hm.should_emit_report(snapshot, previous_state={}) is True
