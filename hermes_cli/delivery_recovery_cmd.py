"""Narrow canonical operator interface; never accepts SQL or a database override.

Use the installed Hermes console entrypoint belonging to the target gateway
release/runtime. An apply records intent only; the owning worker does all I/O.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys


def cmd_delivery_recovery(args: argparse.Namespace) -> int:
    from hermes_constants import get_hermes_home
    from gateway.delivery_recovery import inspect_obligation, request_retry

    try:
        home = Path(get_hermes_home()).absolute()
        expected = Path(args.expect_home)
        # Confirmation, not an alternate routing mechanism. Sticky profiles,
        # --profile and inherited environments must agree with operator intent.
        if (not expected.is_absolute() or expected.resolve() != expected
                or home.resolve() != home or home != expected):
            raise ValueError('profile identity mismatch')
        if not args.obligation_id.strip() or len(args.obligation_id) > 256:
            raise ValueError('one bounded exact obligation ID is required')
        if args.recovery_action == 'inspect':
            result = inspect_obligation(home / 'state.db', args.obligation_id)
        else:
            if not re.fullmatch(r'[0-9a-f]{64}', args.fingerprint):
                raise ValueError('an exact inspection fingerprint is required')
            result = request_retry(home / 'state.db', args.obligation_id,
                fingerprint=args.fingerprint, operator=args.operator,
                acknowledge_possible_duplicate=args.acknowledge_possible_duplicate,
                dry_run=not args.apply)
        result['profile_home'] = str(home)
        result['obligation_id'] = args.obligation_id
    except (OSError, ValueError, sqlite3.Error):
        # Do not disclose private DB errors, trigger text, or payloads.
        print('Recovery refused; confirm profile, inspect the exact obligation, '
              'and review its fingerprint, owner and state.', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def register_cli(subparsers) -> None:
    parser = subparsers.add_parser('delivery-recovery', allow_abbrev=False,
        help='Inspect or request an acknowledged retry of one stranded final reply')
    parser.set_defaults(func=cmd_delivery_recovery)
    actions = parser.add_subparsers(dest='recovery_action', required=True)
    for name in ('inspect', 'request-retry'):
        action = actions.add_parser(name, allow_abbrev=False)
        action.add_argument('obligation_id', help='One exact obligation ID; no bulk selector')
        action.add_argument('--expect-home', required=True,
            help='Confirm the absolute canonical active profile directory (not a DB override)')
        if name == 'request-retry':
            action.add_argument('--fingerprint', required=True)
            action.add_argument('--operator', required=True)
            action.add_argument('--acknowledge-possible-duplicate', required=True,
                                action='store_true')
            action.add_argument('--apply', action='store_true',
                help='Record one audited intent for the owning gateway; default is read-only dry run')
