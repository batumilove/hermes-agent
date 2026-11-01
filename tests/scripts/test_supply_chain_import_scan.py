"""Execute the critical scanner against real direct and protected linear imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ".github/upstream-base"
PROOF_PATH = ".github/upstream-sync-provenance.json"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True, timeout=30).stdout.strip()


def _commit(repo, message):
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _encoded_execution():
    prefix = "exec("
    return prefix + "base64.b64decode('cGFzcw=='))\n"


def _encoded_subprocess():
    prefix = "subprocess.run("
    return prefix + "base64.b64decode('cGFzcw=='))\n"


@pytest.fixture(params=["direct", "linear", "squash", "rebase"])
def scan_import(tmp_path, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-qb", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.test")
    (repo / "original.txt").write_text("original\n", encoding="utf-8")
    original = _commit(repo, "original upstream")
    (repo / ".github").mkdir()
    (repo / BASE_PATH).write_text(original + "\n", encoding="utf-8")
    (repo / "fork.txt").write_text("retained fork\n", encoding="utf-8")
    previous = _commit(repo, "fork target")
    _git(repo, "checkout", "-qb", "official", original)
    (repo / "launcher.py").write_text(_encoded_execution(), encoding="utf-8")
    official = _commit(repo, "upstream shell transport")
    _git(repo, "checkout", "-qb", "source", previous)
    _git(repo, "merge", "--no-ff", "official", "-m", "source merge")
    (repo / BASE_PATH).write_text(official + "\n", encoding="utf-8")
    source = _commit(repo, "record accepted upstream")
    if request.param != "direct":
        tree = _git(repo, "rev-parse", "HEAD^{tree}")
        _git(repo, "checkout", "-qb", "review", previous)
        _git(repo, "read-tree", "--reset", "-u", source)
        _commit(repo, "linear import")
        proof = dict(version=2, upstream_base=official, source_head=source,
                     import_tree=tree, fetch_depth=8)
        (repo / PROOF_PATH).write_text(json.dumps(proof) + "\n", encoding="utf-8")
        _commit(repo, "import proof")
        if request.param == "squash":
            _git(repo, "checkout", "-qb", "landed", previous)
            _git(repo, "merge", "--squash", "review")
            _commit(repo, "protected squash")
        elif request.param == "rebase":
            _git(repo, "checkout", "-qb", "advanced", previous)
            _git(repo, "commit", "--allow-empty", "-qm", "unchanged target advance")
            advanced = _git(repo, "rev-parse", "HEAD")
            _git(repo, "checkout", "review")
            _git(repo, "rebase", "--onto", advanced, previous)
            previous = advanced
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "config", f"url.file://{repo}.insteadOf", "https://github.com/NousResearch/hermes-agent.git")
    # The literal official URL fetch must resolve to the official branch.
    _git(repo, "update-ref", "refs/heads/main", official)
    (repo / "scripts").mkdir()
    for name in ("fork_check_base.py", "fork_provenance.py"):
        shutil.copyfile(ROOT / "scripts" / name, repo / "scripts" / name)
    (repo / "hermes_cli").mkdir()
    for name in ("__init__.py", "fork_provenance.py"):
        shutil.copyfile(ROOT / "hermes_cli" / name, repo / "hermes_cli" / name)
    return repo, previous, tip, official


def _run_scan(repo, base, head, tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/supply-chain-audit.yml").read_text(encoding="utf-8-sig"))
    scan = next(step["run"] for step in workflow["jobs"]["scan"]["steps"] if step.get("id") == "scan")
    scan = scan.replace("${{ github.event.pull_request.base.sha }}", base)
    scan = scan.replace("${{ github.event.pull_request.head.sha }}", head)
    output = tmp_path / "scan-output"
    output.unlink(missing_ok=True)
    environment = dict(os.environ, GITHUB_OUTPUT=str(output))
    result = subprocess.run(["bash", "-c", scan], cwd=repo, env=environment,
                            capture_output=True, text=True, check=False, timeout=30)
    return result, output.read_text(encoding="utf-8") if output.exists() else ""


def test_verified_initial_import_does_not_rescan_unchanged_official_payload(scan_import, tmp_path):
    repo, base, head, _official = scan_import
    result, output = _run_scan(repo, base, head, tmp_path)
    assert result.returncode == 0, result.stderr
    assert output == "found=false\n"


def test_initial_import_still_scans_retained_fork_payload(scan_import, tmp_path):
    repo, base, old_tip, _official = scan_import
    proof_file = repo / PROOF_PATH
    proof = json.loads(proof_file.read_bytes()) if proof_file.exists() else None
    _git(repo, "checkout", "source")
    (repo / "risk.py").write_text(_encoded_execution(), encoding="utf-8")
    _git(repo, "add", "risk.py")
    _git(repo, "commit", "-qm", "retained fork payload in source")
    source = _git(repo, "rev-parse", "HEAD")
    if proof is not None:
        tree = _git(repo, "rev-parse", "HEAD^{tree}")
        _git(repo, "checkout", "--detach", old_tip)
        _git(repo, "read-tree", "--reset", "-u", source)
        proof.update(source_head=source, import_tree=tree)
        proof_file.write_text(json.dumps(proof) + "\n", encoding="utf-8")
        _git(repo, "add", PROOF_PATH)
        _git(repo, "commit", "-qm", "review source with retained fork payload")
    head = _git(repo, "rev-parse", "HEAD")
    result, output = _run_scan(repo, base, head, tmp_path)
    assert result.returncode == 0, result.stderr
    assert output == "found=true\n"


@pytest.mark.parametrize("payload", [_encoded_execution, _encoded_subprocess])
def test_later_fork_payload_is_still_critical(scan_import, tmp_path, payload):
    repo, _previous, base, _official = scan_import
    (repo / "risk.py").write_text(payload(), encoding="utf-8")
    _git(repo, "add", "risk.py")
    _git(repo, "commit", "-qm", "later fork payload")
    head = _git(repo, "rev-parse", "HEAD")
    result, output = _run_scan(repo, base, head, tmp_path)
    assert result.returncode == 0, result.stderr
    assert output == "found=true\n"


def test_fork_commit_cannot_be_promoted_to_official_scan_baseline(scan_import, tmp_path):
    repo, base, _head, _official = scan_import
    (repo / "risk.py").write_text(_encoded_execution(), encoding="utf-8")
    _git(repo, "add", "risk.py")
    _git(repo, "commit", "-qm", "fork payload")
    forged = _git(repo, "rev-parse", "HEAD")
    (repo / BASE_PATH).write_text(forged + "\n", encoding="utf-8")
    _git(repo, "add", BASE_PATH)
    _git(repo, "commit", "-qm", "forge official baseline")
    head = _git(repo, "rev-parse", "HEAD")
    result, output = _run_scan(repo, base, head, tmp_path)
    assert result.returncode != 0
    assert output == ""
