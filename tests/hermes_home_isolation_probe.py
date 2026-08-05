"""Explicit subprocess probe for import-time HERMES_HOME isolation.

The filename intentionally does not match normal test discovery.  The parent
test invokes it with an inherited operator-like HERMES_HOME and verifies that
the suite replaces that value before importing cron persistence modules.
"""

from pathlib import Path

from cron.executions import EXECUTIONS_FILE, create_execution
from tests.conftest import HERMES_HOME_AT_CONFTEST_IMPORT, _REAL_KANBAN_ROOT


def test_import_time_cron_ledger_uses_the_test_sandbox():
    sandbox = Path(HERMES_HOME_AT_CONFTEST_IMPORT).resolve()
    assert EXECUTIONS_FILE == sandbox / "cron" / "executions.db"
    assert _REAL_KANBAN_ROOT != sandbox

    create_execution("isolation-probe", source="test")
    assert EXECUTIONS_FILE.exists()
