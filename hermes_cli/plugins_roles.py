"""Plugin host roles and terminal backend registration capabilities."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable
from functools import wraps

logger = logging.getLogger("hermes_cli.plugins")


class PluginRoleContextMixin:
    """Host capability surface for a plugin context."""

    @property
    def runtime_role(self) -> str:
        """Return the host surface role for this plugin context.

        This is a stable capability bit for plugins that need to distinguish
        request-producing surfaces from passive supervisors. We prefer an
        explicit role when the host set one, and otherwise fall back to the
        safest available inference.
        """
        explicit = (getattr(self._manager, "_runtime_role", None) or "").strip().lower()
        if explicit:
            return explicit
        cli = getattr(self._manager, "_cli_ref", None)
        if cli is not None:
            return "cli"
        for env_name in (
            "HERMES_DASHBOARD_SESSION_TOKEN",
            "HERMES_DASHBOARD_READY",
            "HERMES_DASHBOARD_PUBLIC_URL",
        ):
            if os.getenv(env_name):
                return "dashboard"
        return "unknown"

    @property
    def can_claim_provider_telemetry_writer(self) -> bool:
        """True when this context is allowed to own request telemetry writing.

        Passive dashboard supervisors load plugins but never produce API requests,
        so they must not claim the provider telemetry writer lock. Request-
        producing gateway or CLI surfaces may claim it.
        """
        return self.runtime_role in {"cli", "gateway"}

    def register_environment_backend(
        self,
        name: str,
        factory: Callable,
        *,
        containerized: bool = False,
    ) -> None:
        """Register a terminal backend through the host-owned registry.

        ``factory`` receives the same keyword arguments as
        ``tools.terminal_tool._create_environment``. Built-in backend names
        and names already claimed by another plugin are rejected.
        """
        from tools.environments.registry import register_environment_backend

        if self._load_abandoned:
            return
        register_environment_backend(
            name,
            factory,
            containerized=containerized,
        )
        from tools.environments.registry import unregister_environment_backend
        self._track("environment_backend", name, lambda: unregister_environment_backend(name, factory=factory))
        logger.debug(
            "Plugin %s registered environment backend: %s",
            self.manifest.name,
            name,
        )


class PluginRoleManagerMixin:
    """Track transitions from passive discovery to a request-producing host."""

    @property
    def _cli_ref(self) -> Any:
        """CLI reference used to infer ``cli`` role when no explicit role is set."""
        return self._cli_ref_internal

    @_cli_ref.setter
    def _cli_ref(self, value: Any) -> None:
        self._cli_ref_internal = value
        self._maybe_mark_role_transition_to_claiming()

    @staticmethod
    def _is_claiming_role(role: str) -> bool:
        """Return True when *role* may own the provider telemetry writer."""
        return role in {"cli", "gateway"}

    def _effective_role(self) -> str:
        """Return the role plugins would observe from this manager right now.

        Mirrors :attr:`PluginContext.runtime_role` so transitions are evaluated
        against the same capability bit.
        """
        explicit = (self._runtime_role or "").strip().lower()
        if explicit:
            return explicit
        if self._cli_ref_internal is not None:
            return "cli"
        for env_name in (
            "HERMES_DASHBOARD_SESSION_TOKEN",
            "HERMES_DASHBOARD_READY",
            "HERMES_DASHBOARD_PUBLIC_URL",
        ):
            if os.getenv(env_name):
                return "dashboard"
        return "unknown"

    def _maybe_mark_role_transition_to_claiming(self) -> None:
        """Set the re-discovery flag when the effective role becomes claiming.

        Called when ``_runtime_role`` or ``_cli_ref`` changes after discovery.
        The flag is consumed by ``discover_and_load`` to re-run plugin discovery
        exactly once with the new role, so plugins that gated on the passive
        role get a chance to claim.
        """
        if not self._discovered:
            return
        old_role = self._discovered_role
        new_role = self._effective_role()
        if (
            not self._is_claiming_role(old_role)
            and self._is_claiming_role(new_role)
            and old_role != new_role
        ):
            self._role_transitioned_to_claiming = True

    def set_runtime_role(self, role: str) -> None:
        """Declare the host surface role before plugin discovery.

        Passive supervisors (e.g. the dashboard) should not set this to a
        request-producing role. Plugins may inspect ``ctx.runtime_role`` or
        ``ctx.can_claim_provider_telemetry_writer`` to gate behavior.

        When this is called after plugin discovery and changes the effective
        role from passive/unknown to a request-producing role, the next
        ``discover_plugins()`` call will re-run plugin discovery exactly once
        so role-aware plugins can re-register.
        """
        self._runtime_role = str(role or "").strip().lower()
        self._maybe_mark_role_transition_to_claiming()



def _ignore_after_abandoned_load(method):
    """Turn a registrar into a no-op once the context's load timed out: the abandoned worker thread may
    still be executing register(), and a late registration would land in registries that the failure
    path already swept (#108139)."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if getattr(self, "_load_abandoned", False):
            logger.warning(
                "Plugin '%s' called %s() after its load timed out; ignored", self.manifest.name,
                method.__name__,
            )
            return None
        return method(self, *args, **kwargs)

    return wrapped
