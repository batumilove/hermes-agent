"""Detached worker ownership must survive setup before the real job-entry gate."""
import json
import sqlite3

import pytest


@pytest.mark.parametrize("revocation", ["missing", "terminal", "process_id", "pid", "fingerprint", "unchanged"])
def test_real_worker_entry_revalidates_before_dispatch(tmp_path, monkeypatch, revocation):
    from cron import executions as e, scheduler as s

    monkeypatch.setattr(e, "EXECUTIONS_FILE", tmp_path / "executions.db")
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda *a: None)
    monkeypatch.setattr("gateway.run_startup.GatewayStartupMixin._register_config_hooks", lambda *a, **k: None)
    row = e.create_execution("entry", source="test")
    e.mark_execution_handoff_pending(row["id"])
    payload, ack = tmp_path / "payload.json", tmp_path / "ready"
    payload.write_text(json.dumps({"job": {"id": "entry", "execution_id": row["id"]},
                                   "profile_home": str(tmp_path / "profile")}))
    effects = []
    original_scope = s._install_fire_secret_scope

    def revoke_during_setup():
        # Real run_one_job has begun, after the wrapper's last ownership read.
        assert ack.exists()
        with sqlite3.connect(e.EXECUTIONS_FILE) as conn:
            if revocation == "missing":
                conn.execute("DELETE FROM executions WHERE id=?", (row["id"],))
            elif revocation == "terminal":
                conn.execute("UPDATE executions SET status='failed' WHERE id=?", (row["id"],))
            elif revocation == "process_id":
                conn.execute("UPDATE executions SET process_id='replacement' WHERE id=?", (row["id"],))
            elif revocation == "pid":
                conn.execute("UPDATE executions SET pid=-1 WHERE id=?", (row["id"],))
            elif revocation == "fingerprint":
                conn.execute("UPDATE executions SET process_started_at=-1 WHERE id=?", (row["id"],))
        return original_scope()

    monkeypatch.setattr(s, "_install_fire_secret_scope", revoke_during_setup)
    monkeypatch.setattr(s, "claim_dispatch", lambda *a: effects.append("dispatch") or True)

    class JobReached(BaseException):
        pass

    def run_job(*a, **k):
        effects.append("job")
        raise JobReached()

    monkeypatch.setattr(s, "run_job", run_job)
    if revocation == "unchanged":
        with pytest.raises(JobReached):
            s._run_external_worker_payload(payload, ack)
        assert effects == ["dispatch", "job"]
    else:
        assert s._run_external_worker_payload(payload, ack) is True
        assert effects == []
