from __future__ import annotations

import pytest

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from tools.environments.registry import (
    get_environment_backend,
    register_environment_backend,
    unregister_environment_backend,
)


def test_registry_rejects_builtin_backend_names() -> None:
    with pytest.raises(ValueError, match="reserved"):
        register_environment_backend("docker", lambda **_kwargs: object())


def test_plugin_context_registers_environment_backend() -> None:
    manager = PluginManager()
    context = PluginContext(
        PluginManifest(name="environment-test", source="user"),
        manager,
    )

    def factory(**kwargs):
        return kwargs

    try:
        context.register_environment_backend(
            "test_remote",
            factory,
            containerized=True,
        )
        entry = get_environment_backend("test_remote")
        assert entry is not None
        assert entry.factory is factory
        assert entry.containerized is True
    finally:
        unregister_environment_backend("test_remote", factory=factory)


def test_terminal_factory_dispatches_registered_backend() -> None:
    from tools.terminal_tool import _create_environment

    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return "custom-environment"

    register_environment_backend("test_factory", factory)
    try:
        result = _create_environment(
            "test_factory",
            "image",
            "/workspace",
            45,
            container_config={"container_cpu": 2},
            task_id="task-1",
        )
    finally:
        unregister_environment_backend("test_factory", factory=factory)

    assert result == "custom-environment"
    assert seen["cwd"] == "/workspace"
    assert seen["timeout"] == 45
    assert seen["task_id"] == "task-1"
    assert seen["container_config"]["container_cpu"] == 2
