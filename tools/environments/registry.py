"""Registry for plugin-provided terminal execution environments."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Callable


EnvironmentFactory = Callable[..., Any]
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_RESERVED_BACKENDS = frozenset(
    {"daytona", "docker", "local", "modal", "singularity", "ssh", "vercel_sandbox"}
)


@dataclass(frozen=True)
class EnvironmentBackend:
    name: str
    factory: EnvironmentFactory
    containerized: bool = False


_lock = threading.RLock()
_backends: dict[str, EnvironmentBackend] = {}


def register_environment_backend(
    name: str,
    factory: EnvironmentFactory,
    *,
    containerized: bool = False,
) -> EnvironmentBackend:
    """Register a named backend without allowing built-in replacement."""
    normalized = (name or "").strip().lower()
    if not _NAME_RE.fullmatch(normalized):
        raise ValueError(
            "environment backend name must start with a lowercase letter and "
            "contain only lowercase letters, digits, underscores, or hyphens"
        )
    if normalized in _RESERVED_BACKENDS:
        raise ValueError(f"environment backend name is reserved: {normalized}")
    if not callable(factory):
        raise TypeError("environment backend factory must be callable")

    entry = EnvironmentBackend(
        name=normalized,
        factory=factory,
        containerized=bool(containerized),
    )
    with _lock:
        existing = _backends.get(normalized)
        if existing is not None:
            if existing.factory is factory and existing.containerized == entry.containerized:
                return existing
            raise ValueError(f"environment backend is already registered: {normalized}")
        _backends[normalized] = entry
    return entry


def get_environment_backend(name: str) -> EnvironmentBackend | None:
    with _lock:
        return _backends.get((name or "").strip().lower())


def registered_environment_backends() -> tuple[str, ...]:
    with _lock:
        return tuple(sorted(_backends))


def is_containerized_environment_backend(name: str) -> bool:
    entry = get_environment_backend(name)
    return bool(entry and entry.containerized)


def unregister_environment_backend(
    name: str,
    *,
    factory: EnvironmentFactory | None = None,
) -> bool:
    """Remove a backend, optionally only when it belongs to *factory*."""
    normalized = (name or "").strip().lower()
    with _lock:
        existing = _backends.get(normalized)
        if existing is None or (factory is not None and existing.factory is not factory):
            return False
        del _backends[normalized]
    return True
