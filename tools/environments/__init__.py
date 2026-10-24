"""Hermes execution environment backends: one BaseEnvironment ABC for running shell commands
in a specific context (local, Docker, SSH, Modal direct/Nous-managed, Daytona, Vercel Sandbox;
plugin backends register through ``agent.terminal_env_registry``). ``terminal_tool._create_environment``
selects the backend from TERMINAL_ENV."""

from tools.environments.base import BaseEnvironment
from tools.environments.registry import (
    EnvironmentBackend,
    get_environment_backend,
    is_containerized_environment_backend,
    register_environment_backend,
    registered_environment_backends,
    unregister_environment_backend,
)

__all__ = [
    "BaseEnvironment",
    "EnvironmentBackend",
    "get_environment_backend",
    "is_containerized_environment_backend",
    "register_environment_backend",
    "registered_environment_backends",
    "unregister_environment_backend",
]
