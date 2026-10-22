from __future__ import annotations

import subprocess
import hashlib
import importlib
import json
import re
import shutil
import types
from dataclasses import replace
import sys
from pathlib import Path

import pytest
import yaml

from scripts.install_batumi_components import (
    Component,
    ComponentError,
    checkout_component,
    load_components,
    install_component,
)

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / ".github" / "batumi-components.lock.yaml"


def test_installer_entrypoint_imports_repo_modules_outside_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "install_batumi_components.py"), "--help"],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--verify-only" in result.stdout


def test_component_lock_pins_every_external_repository() -> None:
    components = load_components(LOCK)
    assert len({component.component_id for component in components}) == len(components)
    for component in components:
        assert component.url.removesuffix(".git").rstrip("/") == f"https://github.com/{component.repository}"
        if component.kind == "catalog-plugin":
            assert component.subdir and component.overlay and len(component.plugins) == 1
    assert all(len(component.commit) == 40 for component in components)


def test_operational_workflows_pin_image_inputs_and_locked_deployment() -> None:
    components = load_components(LOCK)
    ops_sha = next(
        component.commit
        for component in components
        if component.repository == "batumilove/hermes-ops"
    )
    workflows = [
        ROOT / ".github" / "workflows" / "deploy-compose.yml",
        ROOT / ".github" / "workflows" / "promote-compose.yml",
        ROOT / ".github" / "workflows" / "stack-ci-shadow.yml",
        ROOT
        / ".github"
        / "workflows"
        / "staging-telegram-socket-diagnostics.yml",
    ]
    for workflow in workflows:
        jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
        calls = {name: job for name, job in jobs.items()
                 if job.get("uses", "").startswith("batumilove/hermes-ops/")}
        assert calls
        for name, job in calls.items():
            reusable, sha = job["uses"].rsplit("@", 1)
            assert re.fullmatch(r"[0-9a-f]{40}", sha)
            if workflow.name == "deploy-compose.yml" and name == "publish":
                assert reusable == "batumilove/hermes-ops/.github/workflows/build-image.yml"
            else:
                assert sha == ops_sha
            if workflow.name in {"deploy-compose.yml", "promote-compose.yml"}:
                assert job["with"]["ops_sha"] == sha


def test_manual_promotion_requires_exact_digest_for_rollback() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "promote-compose.yml").read_text(
            encoding="utf-8"
        )
    )
    triggers = workflow.get("on", workflow.get(True))
    image_digest = triggers["workflow_dispatch"]["inputs"]["image_digest"]
    assert image_digest["required"] is True


def test_deploy_workflow_allows_pre_cutover_candidate_dispatch() -> None:
    workflow = ROOT / ".github" / "workflows" / "deploy-compose.yml"
    assert "workflow_dispatch:" in workflow.read_text(encoding="utf-8")


def test_component_lock_rejects_moving_refs(tmp_path: Path) -> None:
    payload = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    payload["components"][0]["commit"] = "main"
    invalid = tmp_path / "lock.yaml"
    invalid.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ComponentError, match="full lowercase SHA"):
        load_components(invalid)


def test_checkout_verifies_exact_detached_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "README.md").write_text("pinned\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    component = Component(
        "fixture",
        "local/fixture",
        str(source),
        commit,
        "operations",
        (),
    )
    destination = tmp_path / "checkout"
    checkout_component(component, destination)
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=destination,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == commit
    )
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=destination,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


@pytest.fixture
def catalog_component(tmp_path):
    """Exact reviewed Honcho files in a clean local checkout; no remote install or SDK needed."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    plugin = checkout / "hermes-plugin-honcho"
    fixture = ROOT / "tests/plugins/external/fixtures/honcho-e2d2aba"
    shutil.copytree(fixture, plugin)
    (plugin / "plugin.yaml").write_text("name: honcho\nkind: standalone\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "test@example.test"],
                 ["config", "user.name", "Test"], ["add", "."], ["commit", "-qm", "pinned fixture"]):
        subprocess.run(["git", *args], cwd=checkout, check=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True,
                            capture_output=True, text=True).stdout.strip()
    registry = json.loads((ROOT / "patches/external-plugins/overlays.json").read_text())
    overlay = next(row for row in registry["overlays"] if row["id"] == "honcho-http-lifecycle")
    overlay["commit"] = commit
    manifest = tmp_path / "overlays.json"
    manifest.write_text(json.dumps({"version": 1, "overlays": [overlay]}), encoding="utf-8")
    component = Component("honcho", "plastic-labs/honcho", "https://github.com/plastic-labs/honcho.git",
                          commit, "catalog-plugin", ("honcho",), "hermes-plugin-honcho", "honcho-http-lifecycle")
    return component, checkout, manifest, tmp_path / "home"


def test_catalog_overlay_repeated_install_preserves_verified_correction(catalog_component):
    component, checkout, manifest, home = catalog_component
    install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    target = home / "plugins/honcho"
    first = json.loads((target / ".batumi-component.json").read_text())
    assert first["commit"] == component.commit
    assert first["overlay"] == component.overlay
    assert (target / "LICENSE").read_bytes() == (checkout / component.subdir / "LICENSE").read_bytes()
    assert first["installed_sha256"] != first["source_sha256"]
    assert not (target / ".git").exists()
    install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    assert json.loads((target / ".batumi-component.json").read_text()) == first
    for name, expected in first["installed_sha256"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == expected
    assert subprocess.run(["git", "status", "--porcelain"], cwd=checkout, capture_output=True,
                          text=True, check=True).stdout == ""


def test_catalog_publication_precedes_enable_and_failure_restores_previous(catalog_component, monkeypatch):
    from scripts import install_batumi_components as installer
    component, checkout, manifest, home = catalog_component
    target = home / "plugins/honcho"
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous plugin", encoding="utf-8")
    original_run = installer._run

    def enable_failure(args, **kwargs):
        if args[1:3] == ["-m", "hermes_cli.main"]:
            assert args[-2:] == ["enable", "honcho"]
            assert kwargs["env"]["HERMES_HOME"] == str(home)
            assert (target / ".batumi-component.json").is_file()
            assert not (target / "previous.txt").exists()
            raise ComponentError("fixture enable failure")
        return original_run(args, **kwargs)

    monkeypatch.setattr(installer, "_run", enable_failure)
    with pytest.raises(ComponentError, match="enable failure"):
        install_component(component, checkout, hermes_home=home, enable_plugins=True, overlay_manifest=manifest)
    assert (target / "previous.txt").read_text() == "previous plugin"
    assert not (target / ".batumi-component.json").exists()
    assert not list((home / "plugins").glob(".honcho.batumi-stage-*"))


@pytest.mark.parametrize("field,value", [
    ("repository", "other/owner"), ("url", "https://github.com/other/owner.git"),
    ("commit", "0" * 40), ("subdir", "other-subdir"),
])
def test_catalog_overlay_identity_mismatch_preserves_previous(catalog_component, field, value):
    component, checkout, manifest, home = catalog_component
    target = home / "plugins/honcho"
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous", encoding="utf-8")
    with pytest.raises(ComponentError, match="identity mismatch"):
        install_component(replace(component, **{field: value}), checkout, hermes_home=home,
                          enable_plugins=False, overlay_manifest=manifest)
    assert (target / "previous.txt").read_text() == "previous"
    assert not (target / ".batumi-component.json").exists()


@pytest.mark.parametrize("which", ["patch", "source"])
def test_catalog_overlay_hash_mismatch_never_publishes(catalog_component, which):
    component, checkout, manifest, home = catalog_component
    document = json.loads(manifest.read_text())
    row = document["overlays"][0]
    if which == "patch":
        row["sha256"] = "0" * 64
    else:
        row["source_sha256"]["client.py"] = "0" * 64
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ComponentError, match="SHA256 mismatch"):
        install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    assert not (home / "plugins/honcho").exists()


@pytest.mark.parametrize("subdir", ["../honcho", "/tmp/honcho", "honcho/../escape", "honcho\\escape", "C:/honcho"])
def test_catalog_component_rejects_path_traversal(tmp_path, subdir):
    payload = {"version": 1, "components": [{
        "id": "honcho", "repository": "plastic-labs/honcho", "url": "https://github.com/plastic-labs/honcho.git",
        "commit": "a" * 40, "kind": "catalog-plugin", "plugins": ["honcho"],
        "subdir": subdir, "overlay": "honcho-http-lifecycle",
    }]}
    lock = tmp_path / "lock.yaml"
    lock.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ComponentError, match="repository-relative"):
        load_components(lock)


def test_catalog_installed_provider_owns_and_closes_http_transport(catalog_component, monkeypatch):
    component, checkout, manifest, home = catalog_component
    install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    package = types.ModuleType("installed_honcho_test")
    package.__path__ = [str(home / "plugins/honcho")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in list(sys.modules):
        if name.startswith("installed_honcho_test."):
            monkeypatch.delitem(sys.modules, name)
    provider = importlib.import_module("installed_honcho_test.client")
    monkeypatch.setattr(provider, "_refresh_oauth", lambda *args, **kwargs: None)
    monkeypatch.setattr("hermes_cli.config.load_config", dict)
    sdk = types.ModuleType("honcho")

    class HTTPWrapper:
        def __init__(self, client):
            self._client = client
            self._owns_client = False
            self.close_count = 0

        def close(self):
            self.close_count += 1
            self._client.close()

    class Honcho:
        def __init__(self, **kwargs):
            self._http = HTTPWrapper(kwargs["http_client"])
            self._async_http = None

    sdk.Honcho = Honcho
    monkeypatch.setitem(sys.modules, "honcho", sdk)
    config = provider.HonchoClientConfig(api_key="test-key", workspace_id="test-workspace",
                                         environment="production", timeout=11.0)
    client = provider.get_honcho_client(config)
    assert client._http._owns_client
    provider.reset_honcho_client()
    provider.reset_honcho_client()
    assert client._http.close_count == 1
    assert client._http._client.is_closed


def test_catalog_publication_failure_restores_previous_directory(catalog_component, monkeypatch):
    from scripts import install_batumi_components as installer
    component, checkout, manifest, home = catalog_component
    target = home / "plugins/honcho"
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous", encoding="utf-8")
    original_replace = installer.os.replace

    def refuse_candidate(source, destination):
        if Path(source).name == "candidate":
            raise OSError("fixture publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", refuse_candidate)
    with pytest.raises(ComponentError, match="publication failure"):
        install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    assert (target / "previous.txt").read_text() == "previous"
    assert not list((home / "plugins").glob(".honcho.batumi-stage-*"))


def test_catalog_failed_rollback_retains_recovery_copy(catalog_component, monkeypatch):
    from scripts import install_batumi_components as installer
    component, checkout, manifest, home = catalog_component
    target = home / "plugins/honcho"
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous", encoding="utf-8")
    original_replace = installer.os.replace

    def refuse_publication_and_restore(source, destination):
        if Path(source).name == "candidate" or Path(source).name.endswith(".previous"):
            raise OSError("fixture filesystem failure")
        return original_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", refuse_publication_and_restore)
    with pytest.raises(ComponentError, match="previous plugin retained"):
        install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    backups = list((home / "plugins").glob(".honcho.batumi-stage-*.previous"))
    assert len(backups) == 1
    assert (backups[0] / "previous.txt").read_text() == "previous"


@pytest.mark.parametrize("field", ["patch", "source_sha256"])
def test_catalog_overlay_rejects_traversing_file_paths(catalog_component, field):
    component, checkout, manifest, home = catalog_component
    document = json.loads(manifest.read_text())
    row = document["overlays"][0]
    if field == "patch":
        row["patch"] = "../unreviewed.patch"
    else:
        row["source_sha256"] = {"../unreviewed.py": "0" * 64}
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ComponentError, match="repository-relative"):
        install_component(component, checkout, hermes_home=home, enable_plugins=False, overlay_manifest=manifest)
    assert not (home / "plugins/honcho").exists()



def test_catalog_verify_only_checks_overlay_without_publishing(catalog_component, monkeypatch, tmp_path):
    from scripts import install_batumi_components as installer
    component, checkout, manifest, home = catalog_component
    lock = tmp_path / "components.yaml"
    lock.write_text(yaml.safe_dump({"version": 1, "components": [{
        "id": component.component_id, "repository": component.repository, "url": component.url,
        "commit": component.commit, "kind": component.kind, "plugins": list(component.plugins),
        "subdir": component.subdir, "overlay": component.overlay,
    }]}), encoding="utf-8")
    monkeypatch.setattr(installer, "DEFAULT_OVERLAYS", manifest)
    monkeypatch.setattr(installer, "checkout_component", lambda _component, dest: shutil.copytree(checkout, dest))
    monkeypatch.setattr(installer, "install_component", lambda *args, **kwargs: pytest.fail("verify-only published"))
    assert installer.main(["--lock", str(lock), "--cache-dir", str(tmp_path / "cache"),
                           "--hermes-home", str(home), "--verify-only"]) == 0
    assert not (home / "plugins").exists()


def test_catalog_checkout_keeps_hashable_blob_bytes_under_host_autocrlf(catalog_component, tmp_path, monkeypatch):
    component, checkout, _manifest, _home = catalog_component
    git_config = tmp_path / "gitconfig"
    git_config.write_text("[core]\n    autocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    source_bytes = (checkout / component.subdir / "client.py").read_bytes()
    local_component = replace(component, url=str(checkout))
    destination = tmp_path / "fetched"
    checkout_component(local_component, destination)
    assert (destination / component.subdir / "client.py").read_bytes() == source_bytes
