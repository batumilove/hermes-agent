"""Factory-backed per-agent state initialization."""
from typing import Any

def _set_defaults(agent, table: dict[str, Any]) -> None:
    """Assign each ``name -> value`` on ``agent``; callables are factories (fresh per agent)."""
    for name, value in table.items():
        setattr(agent, name, value() if callable(value) else value)
