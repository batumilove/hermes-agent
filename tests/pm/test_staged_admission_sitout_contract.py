"""Migration code publication must distinguish preserved members from new code."""
import pytest

import hermes_cli.plugins_manifest as gate
import pm.client
import pm.plugins_state
from pm.publication import StagedPlugin


def plugin(path, name, requires=""):
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text(f"name: {name}\n{requires}")
    (path / "pyproject.toml").write_text(
        f'[project]\nname="hermes-plugin-{name}"\nversion="1.0.0"\n')
    return path


@pytest.fixture
def migration(tmp_path, monkeypatch):
    root = tmp_path / "state"
    home = root / "profiles" / "allegro-builder-ssh"
    sibling = root / "profiles" / "wg-coder"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [honcho]\n")
    bad = plugin(sibling / "plugins" / "langfuse", "langfuse",
                 "requires_hermes: '>=0.21.7'\n")
    (sibling / "config.yaml").write_text("plugins:\n  enabled: [langfuse]\n")
    staged = plugin(tmp_path / "staged-honcho", "honcho")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_DEPENDENCY_HOME", str(root))
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [home, sibling])
    monkeypatch.setattr(gate, "running_hermes_version", lambda: "0.10.0")
    return home, sibling, staged, bad


def request(home, staged, name="honcho"):
    return {"target": str(home / "plugins" / name), "staged": str(staged),
            "old_metadata": {}, "new_metadata": {name: {"revision": "fixture"}},
            "target_digest": None}


def test_honcho_migration_publication_preserves_incompatible_sibling(migration):
    home, sibling, staged, bad = migration
    before = (sibling / "config.yaml").read_bytes()
    selection = StagedPlugin(request(home, staged))
    assert selection.active
    assert selection.members == {(home / "plugins" / "honcho").resolve(): staged.resolve()}
    assert bad.resolve() not in selection.members
    assert (sibling / "config.yaml").read_bytes() == before


def test_new_langfuse_migration_code_still_refused(migration):
    home, _sibling, staged, _bad = migration
    (home / "config.yaml").write_text("plugins:\n  enabled: [langfuse]\n")
    (staged / "plugin.yaml").write_text("name: langfuse\nrequires_hermes: '>=0.21.7'\n")
    with pytest.raises(ValueError, match="requires hermes >=0.21.7, running 0.10.0"):
        StagedPlugin(request(home, staged, "langfuse"))


@pytest.mark.parametrize("manifest", ["manifest_version: 99\n",
                                      "requires_hermes: '>=0.21.7'\nmanifest_version: 99\n"])
def test_staged_preserved_sibling_other_validation_still_refuses(migration, manifest):
    home, _sibling, staged, bad = migration
    (bad / "plugin.yaml").write_text("name: langfuse\n" + manifest)
    with pytest.raises(ValueError):
        StagedPlugin(request(home, staged))


def test_replacing_incompatible_existing_target_with_new_incompatible_code_refuses(migration):
    _home, sibling, staged, _bad = migration
    (staged / "plugin.yaml").write_text("name: langfuse\nrequires_hermes: '>=0.21.7'\n")
    with pytest.raises(ValueError, match="requires hermes"):
        StagedPlugin(request(sibling, staged, "langfuse"))


def test_publish_plugin_submits_staged_update_not_selection(migration, monkeypatch):
    from hermes_cli.plugins_transaction import publish_plugin
    from pm.plugin_inputs import StagedUpdate

    home, _sibling, staged, _bad = migration
    captured = []

    def worker_boundary(*, explicit, plugins):
        assert explicit
        assert isinstance(plugins, StagedUpdate)
        captured.append(plugins)
        # Exercise actual worker admission, stopping before resolver/publication.
        StagedPlugin(plugins.data)

    monkeypatch.setattr(pm.client, "sync_venv", worker_boundary)
    publish_plugin(staged, home / "plugins" / "honcho", {},
                   {"honcho": {"revision": "fixture"}})
    assert len(captured) == 1


def test_staged_preserved_sibling_invalid_dependency_declaration_refuses(migration):
    home, _sibling, staged, bad = migration
    (bad / "pyproject.toml").write_text("not valid toml")
    with pytest.raises(ValueError):
        StagedPlugin(request(home, staged))
