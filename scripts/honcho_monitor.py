#!/usr/bin/env python3
"""Honcho production smoke monitor.

Standalone cron/watchdog entrypoint for the Honcho smoke report.
"""

from hermes_cli.honcho_monitor import main


if __name__ == "__main__":
    raise SystemExit(main())
