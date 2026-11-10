"""Admission epochs prevent paused or staged writes crossing a repair boundary."""
from contextvars import copy_context
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
import hashlib
import json
import os
import subprocess
import sys

import pytest

from tools import skill_manager_tool as smt
from tools import skill_write_fence as fence
from tools.registry import registry

pytestmark = pytest.mark.platforms("posix")
SKILL = "---\nname: {name}\ndescription: Use when testing isolated skill admission.\n---\n# Test\nOriginal.\n"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (home / "hermes-runtime-tree.lock").touch(mode=0o600)
    state = home / ".skills.shared-skills-state"
    state.mkdir(mode=0o700)
    (state / "lock").touch(mode=0o600)
    (tmp_path / "capabilities").mkdir(mode=0o700)
    return home


def dispatch(**kwargs):
    return json.loads(registry.dispatch("skill_manage", kwargs))


def create(home, name="fence-example"):
    result = dispatch(action="create", name=name, content=SKILL.format(name=name))
    assert result["success"], result
    return home / "skills" / name / "SKILL.md"


def begin(home, names=("fence-example",), receipt_name="capability.json"):
    return fence.begin(names, expected_state_sha256=fence.status()["state_sha256"],
                       plan_sha256="a" * 64, owner="test-operator",
                       expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                       receipt=home.parent / "capabilities" / receipt_name)


def end(home, started, receipt_name="capability.json"):
    outcome = home / "outcome.json"
    outcome.write_text(json.dumps({"lease_id": started["lease_id"], "plan_sha256": "a" * 64,
                                   "selected": started["selected"], "status": "COMPLETE"}))
    outcome.chmod(0o600)
    return fence.end(home.parent / "capabilities" / receipt_name, expected_state_sha256=fence.status()["state_sha256"],
                     outcome=outcome, outcome_sha256=hashlib.sha256(outcome.read_bytes()).hexdigest())


def test_registry_refuses_fenced_scope_but_unrelated_package_progresses(profile):
    target = create(profile)
    other = create(profile, "fence-unrelated")
    started = begin(profile, ["devops/fence-example"])
    result = dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Bad.")
    assert result["skill_write_fenced"] and not result["success"]
    result = dispatch(action="patch", name="fence-unrelated", old_string="Original.", new_string="Allowed.")
    assert result["success"], result
    assert "Original." in target.read_text() and "Allowed." in other.read_text()
    end(profile, started)
    assert dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Fresh.")["success"]


@pytest.mark.parametrize("batch", [False, True])
def test_admitted_flat_or_batch_stays_stale_after_fence_release(profile, monkeypatch, batch):
    target = create(profile)
    entered, release = Event(), Event()
    original = smt._background_review_preflight
    results = []

    def pause(action, name):
        entered.set()
        assert release.wait(10)
        return original(action, name)

    # Batch validation imports the same guard from its defining module.
    monkeypatch.setattr(smt, "_background_review_preflight", pause)
    if batch:
        monkeypatch.setattr("tools.skill_manager_guards._background_review_preflight", pause)
    call = (lambda: dispatch(action="", name="", operations=[{
        "action": "patch", "name": "fence-example", "old_string": "Original.", "new_string": "Stale."}])) if batch else (
        lambda: dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Stale."))
    context = copy_context()
    thread = Thread(target=lambda: context.run(lambda: results.append(call())))
    thread.start()
    try:
        assert entered.wait(10)
        started = begin(profile)
        target.write_text(target.read_text().replace("Original.", "Approved repair."))
        end(profile, started)
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert results and results[0]["skill_write_fenced"], results
    assert "Approved repair." in target.read_text() and "Stale." not in target.read_text()


def test_existing_mutex_writer_prevents_begin_and_preserves_state(profile):
    create(profile)
    before = fence.status()
    with smt._skill_mutation_lock("fence-example"):
        with pytest.raises(fence.FenceError, match="writer or installer"):
            begin(profile)
    assert fence.status() == before
    assert not (profile.parent / "capabilities" / "capability.json").exists()


def test_old_staged_full_rewrite_cannot_get_a_fresh_epoch(profile, monkeypatch):
    target = create(profile)
    from tools import write_approval as wa
    monkeypatch.setattr(wa, "evaluate_gate", lambda subsystem: wa.GateDecision(stage=True, message="test"))
    staged = dispatch(action="edit", name="fence-example", content=SKILL.format(name="fence-example") + "Old payload.")
    pending = wa.get_pending(wa.SKILLS, staged["pending_id"])
    started = begin(profile)
    target.write_text(target.read_text() + "Approved repair.\n")
    end(profile, started)
    result = json.loads(smt.apply_skill_pending(pending["payload"]))
    assert result["skill_write_fenced"] and "Approved repair." in target.read_text()
    assert "Old payload." not in target.read_text()
    assert wa.get_pending(wa.SKILLS, staged["pending_id"]) == pending


def test_new_staged_payload_is_bound_and_replay_without_intervening_fence_works(profile, monkeypatch):
    target = create(profile)
    started = begin(profile)
    end(profile, started)
    from tools import write_approval as wa
    monkeypatch.setattr(wa, "evaluate_gate", lambda subsystem: wa.GateDecision(stage=True, message="test"))
    staged = dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Reviewed.")
    pending = wa.get_pending(wa.SKILLS, staged["pending_id"])
    assert pending["payload"]["_skill_write_admission"]["epochs"]["fence-example"] > 0
    result = json.loads(smt.apply_skill_pending(pending["payload"]))
    assert result["success"] and "Reviewed." in target.read_text()


def test_fence_does_not_change_user_curator_locks(profile):
    create(profile)
    from tools import skill_usage
    skill_usage.set_locked("fence-example", True)
    before = (profile / "skills" / ".usage.json").read_bytes()
    started = begin(profile)
    end(profile, started)
    assert skill_usage.is_locked("fence-example")
    assert (profile / "skills" / ".usage.json").read_bytes() == before


def test_wrong_scope_outcome_and_foreign_cas_do_not_release(profile):
    create(profile)
    started = begin(profile)
    before = fence.status()
    outcome = profile / "outcome.json"
    outcome.write_text(json.dumps({"lease_id": started["lease_id"], "plan_sha256": "b" * 64,
                                   "selected": started["selected"], "status": "COMPLETE"}))
    outcome.chmod(0o600)
    with pytest.raises(fence.FenceError, match="scoped completion"):
        fence.end(profile.parent / "capabilities" / "capability.json", expected_state_sha256=before["state_sha256"],
                  outcome=outcome, outcome_sha256=hashlib.sha256(outcome.read_bytes()).hexdigest())
    assert fence.status() == before
    with pytest.raises(fence.FenceError, match="state changed"):
        fence.begin(["fence-unrelated"], expected_state_sha256="0" * 64, plan_sha256="a" * 64,
                    owner="other", expires_at=(datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
                    receipt=profile.parent / "capabilities" / "other.json")
    assert fence.status() == before


def test_corrupt_or_symlink_state_fails_closed(profile):
    target = create(profile)
    state = profile / "state"
    state.mkdir(mode=0o700)
    path = state / "skill-write-fence.json"
    path.write_text("broken")
    path.chmod(0o600)
    assert dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Bad.")["skill_write_fenced"]
    path.unlink()
    external = profile / "external.json"
    external.write_text('{"version":1,"epochs":{},"leases":{}}')
    external.chmod(0o600)
    path.symlink_to(external)
    assert dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Bad.")["skill_write_fenced"]
    assert "Original." in target.read_text()


def test_failed_batch_still_rolls_back_before_other_control_can_enter(profile):
    target = create(profile)
    original = target.read_bytes()
    result = dispatch(action="", name="", operations=[
        {"action": "patch", "name": "fence-example", "old_string": "Original.", "new_string": "First."},
        {"action": "remove_file", "name": "fence-example", "file_path": "references/missing.md"}])
    assert not result["success"]
    assert target.read_bytes() == original
    started = begin(profile)
    end(profile, started)


def test_batch_with_one_fenced_target_changes_neither_package(profile):
    first = create(profile)
    second = create(profile, "fence-second")
    before = first.read_bytes(), second.read_bytes()
    started = begin(profile, ["fence-second"])
    result = dispatch(action="", name="", operations=[
        {"action": "patch", "name": "fence-example", "old_string": "Original.", "new_string": "Bad."},
        {"action": "patch", "name": "fence-second", "old_string": "Original.", "new_string": "Bad."}])
    assert result["skill_write_fenced"]
    assert (first.read_bytes(), second.read_bytes()) == before
    end(profile, started)


@pytest.mark.parametrize("prior_fence", [False, True])
def test_stage_binding_cannot_cross_profiles(profile, tmp_path, monkeypatch, prior_fence):
    create(profile)
    if prior_fence:
        started = begin(profile)
        end(profile, started)
    from tools import write_approval as wa
    monkeypatch.setattr(wa, "evaluate_gate", lambda subsystem: wa.GateDecision(stage=True, message="test"))
    staged = dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Bad.")
    payload = wa.get_pending(wa.SKILLS, staged["pending_id"])["payload"]
    other = tmp_path / "other-profile"
    other.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(other))
    result = json.loads(smt.apply_skill_pending(payload))
    assert result["skill_write_fenced"] and not (other / "skills").exists()


def test_expiry_never_silently_unfences(profile, monkeypatch):
    create(profile)
    started = begin(profile)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(fence, "datetime", Later)
    assert started["lease_id"] in fence.status()["state"]["leases"]
    assert dispatch(action="patch", name="fence-example", old_string="Original.", new_string="Bad.")["skill_write_fenced"]
    end(profile, started)


def test_published_lease_has_recoverable_receipt_after_operator_crash(profile, monkeypatch):
    create(profile)
    publish = fence._publish

    def crash(state):
        publish(state)
        raise RuntimeError("simulated operator exit after durable publication")

    monkeypatch.setattr(fence, "_publish", crash)
    with pytest.raises(RuntimeError, match="simulated operator"):
        begin(profile)
    monkeypatch.setattr(fence, "_publish", publish)
    capability = json.loads((profile.parent / "capabilities" / "capability.json").read_bytes())
    assert capability["lease_id"] in fence.status()["state"]["leases"]
    end(profile, capability)
    assert not fence.status()["state"]["leases"]


def test_public_cli_returns_no_capability_and_preserves_profile_scope(profile):
    create(profile)
    args = [sys.executable, "-m", "tools.skill_write_fence"]
    env = {**os.environ, "HERMES_HOME": str(profile)}
    cwd = Path(__file__).resolve().parents[2]
    before = subprocess.run(args + ["status"], cwd=cwd, env=env, check=True, capture_output=True, text=True)
    current = json.loads(before.stdout)
    assert current["profile"] == str(profile)
    begun = subprocess.run(args + ["begin", "--name", "fence-example", "--expected-state-sha256",
        current["state_sha256"], "--plan-sha256", "a" * 64, "--owner", "test-operator",
        "--expires-at", (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "--receipt", str(profile.parent / "capabilities" / "capability.json")], cwd=cwd, env=env, check=True, capture_output=True, text=True)
    capability = json.loads((profile.parent / "capabilities" / "capability.json").read_bytes())
    assert capability["token"] not in begun.stdout
    assert (profile.parent / "capabilities" / "capability.json").stat().st_mode & 0o077 == 0
    end(profile, json.loads(begun.stdout))


def test_directory_fence_covers_frontmatter_alias_and_drains_alias_writer(profile):
    target = create(profile)
    target.write_text(target.read_text().replace("name: fence-example", "name: display-alias"))
    assert Path(smt._find_skill("display-alias")["path"]) == target.parent
    before = fence.status()
    with smt._skill_mutation_lock("display-alias"):
        with pytest.raises(fence.FenceError, match="writer or installer"):
            begin(profile)
    assert fence.status() == before
    started = begin(profile)
    result = dispatch(action="patch", name="display-alias", old_string="Original.", new_string="Bad.")
    assert result["skill_write_fenced"] and "Original." in target.read_text()
    end(profile, started)
    assert dispatch(action="patch", name="display-alias", old_string="Original.", new_string="Fresh.")["success"]


@pytest.mark.parametrize("relative", [
    "state/skill-write-fence.json",
    "state/../state/skill-write-fence.json",
    ".skills.shared-skills-state/baseline.json",
    "skills/fence-example/recovery-capability.json",
])
def test_capability_cannot_alias_control_or_installable_state(profile, relative):
    create(profile)
    before = fence.status()
    path = profile / relative
    with pytest.raises(fence.FenceError, match="outside the profile"):
        fence.begin(["fence-example"], expected_state_sha256=before["state_sha256"],
                    plan_sha256="a" * 64, owner="test-operator",
                    expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(), receipt=path)
    assert not path.exists()
    assert fence.status() == before
