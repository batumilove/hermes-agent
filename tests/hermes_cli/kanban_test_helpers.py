"""Shared helpers for synthetic Kanban filesystem state."""

from __future__ import annotations

from pathlib import Path


def create_secure_home(path: Path) -> Path:
    """Create a private synthetic Hermes home without process-global umask changes."""
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path
