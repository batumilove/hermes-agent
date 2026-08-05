"""Cron-test fixtures.

Provides a default ``HERMES_MODEL`` for cron run_job tests so each one
doesn't have to spell out a model. The global conftest blanks
HERMES_MODEL hermetically; without this autouse fixture every cron test
that exercises ``run_job`` would hit the fail-fast guard added in
``cron/scheduler.py`` (see issue #23979) and have to be rewritten.

Tests that specifically need ``HERMES_MODEL`` unset — model-resolution
edge cases — call ``monkeypatch.delenv("HERMES_MODEL", raising=False)``
inside the test, which overrides this fixture's value for that scope.
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _default_cron_test_model(monkeypatch):
    """Pin a default HERMES_MODEL so cron run_job tests have a resolvable model."""
    monkeypatch.setenv("HERMES_MODEL", "test-cron-default-model")
    yield


@pytest.fixture(autouse=True)
def _isolate_activegraph_cron_emission(monkeypatch):
    """Keep cron unit tests from emitting into an operator's ActiveGraph DB.

    ``cron.scheduler`` discovers the optional directory plugin lazily.  The
    plugin's configured DB path may already be bound to the operator profile
    before the global per-test HERMES_HOME fixture runs, so tests exercising
    ``run_one_job`` must default to a local no-op emitter.  Focused bridge tests
    explicitly replace these globals when they need to assert emission.
    """
    scheduler = sys.modules.get("cron.scheduler")
    if scheduler is not None:
        monkeypatch.setattr(scheduler, "_ag_emit", lambda *_args, **_kwargs: None, raising=False)
        monkeypatch.setattr(scheduler, "_ag_import_attempted", True, raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_session_context_vars():
    """Restore session ContextVars around cron tests that call run_job directly.

    Production confines each cron run to a copied context, but direct unit tests
    share the pytest context. ``run_job`` intentionally clears ordinary session
    variables to explicit empty values, which would otherwise shadow legacy env
    fallbacks used by later approval tests in the same process.
    """
    from gateway.session_context import _UNSET, _VAR_MAP

    def _reset_all():
        for var in _VAR_MAP.values():
            var.set(_UNSET)

    _reset_all()
    yield
    _reset_all()
