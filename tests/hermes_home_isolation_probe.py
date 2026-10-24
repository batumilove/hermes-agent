"""Explicit subprocess probe for transaction-time HERMES_HOME isolation.

The filename intentionally does not match normal test discovery. The parent
test invokes it with an inherited operator-like HERMES_HOME and verifies that
the suite replaces that value before cron persistence opens its ledger.
"""

from pathlib import Path

from cron.executions import EXECUTIONS_FILE, create_execution
from hermes_constants import get_hermes_home
from tests.conftest import HERMES_HOME_AT_CONFTEST_IMPORT, _REAL_KANBAN_ROOT


def test_cron_ledger_uses_the_test_sandbox():
    collection_sandbox = Path(HERMES_HOME_AT_CONFTEST_IMPORT).resolve()
    transaction_sandbox = get_hermes_home().resolve()
    ledger = transaction_sandbox / "cron" / "executions.db"

    # None means production resolves the active profile at transaction time;
    # non-None remains an explicit test override.
    assert EXECUTIONS_FILE is None
    assert _REAL_KANBAN_ROOT != collection_sandbox
    assert _REAL_KANBAN_ROOT != transaction_sandbox

    create_execution("isolation-probe", source="test")
    assert ledger.exists()
