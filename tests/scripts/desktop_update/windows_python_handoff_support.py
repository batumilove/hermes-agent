"""The unchanged real published CLI program used by native Windows hand-off tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent


CLI = """
import json, os, sys
from pathlib import Path
def main():
    if '--version' in sys.argv:
        print('Install directory: ' + str(Path(__file__).resolve().parents[1])); return 0
    if '--help' in sys.argv:
        print('--keep-stash'); return 0
    with Path(os.environ['HANDOFF_CALLS']).open('a') as stream:
        stream.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
    print('Desktop build failed')  # no warning-driven second build on PM
    if sys.argv[1:] == ['gateway', 'start', '--all']:
        return int(os.environ.get('GATEWAY_EXIT', '0'))
    if os.environ.get('HANDOFF_HANG'):
        # Print, then park silently: the shape the hand-off's idle watchdog kills.
        sys.stdout.reconfigure(encoding='utf-8')  # as the real CLI does
        print(os.environ['HANDOFF_HANG'].replace('|', '\\n'), flush=True)
        import time; time.sleep(300)
    return int(os.environ['HANDOFF_EXIT'])
if __name__ == '__main__':
    sys.exit(main())
"""
