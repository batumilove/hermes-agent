"""s6 cont-finish: TERM and wait only for identity-verified gateway owners.

No application imports, state writes, process-wide signals or clean markers.
The final s6 process sweep remains responsible for stuck processes.
"""
import json
import os
from pathlib import Path
import signal
import time


def fingerprint(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def main():
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    owners = set()
    # Runtime status survives PID-file removal during teardown. Match kernel
    # start ticks plus the initial home (or exact root/profile launch argv).
    # Never trust PID alone.
    for path in [home / 'gateway_state.json', *home.glob('profiles/*/gateway_state.json')]:
        try:
            record = json.loads(path.read_text())
            if record.get('kind') != 'hermes-gateway':
                continue
            pid, ticks = int(record['pid']), int(record['start_time'])
            if pid <= 1 or fingerprint(pid) != ticks:
                continue
            environ = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
            expected = b'HERMES_HOME=' + os.fsencode(path.parent)
            if expected not in environ:
                # Foreground `hermes -p NAME gateway run --no-supervise`
                # resolves its home after exec, invisible in /proc/environ.
                # Require the initial root AND an exact profile-aware launch
                # argv, in addition to the state kind/PID/kernel start ticks.
                root = path.parent.parent.parent
                if path.parent.parent.name != 'profiles':
                    continue
                if b'HERMES_HOME=' + os.fsencode(root) not in environ:
                    continue
                argv = [os.fsdecode(a) for a in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if a]
                if argv and Path(argv[0]).name.startswith('python'):
                    argv = argv[1:]
                    # scripts/build/launchers.py's packaged CLI bootstrap.
                    # Normalize only this exact prefix, never arbitrary -c code.
                    bootstrap = ("import os, site, sys; sys.argv[0]='hermes'; "
                                 "site.addsitedir(os.environ['HERMES_SITE']); "
                                 "from hermes_cli.main import main; sys.exit(main())")
                    if argv[:3] == ['-P', '-c', bootstrap]:
                        argv = ['hermes', *argv[3:]]
                if not argv or Path(argv[0]).name != 'hermes':
                    continue
                tail = argv[1:]
                if len(tail) != 5 or tail[0] not in ('-p', '--profile') or tail[1:] != [path.parent.name, 'gateway', 'run', '--no-supervise']:
                    continue
            owners.add((pid, ticks))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    deadline = time.monotonic() + 80
    for pid, ticks in owners:
        try:
            if fingerprint(pid) == ticks:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    while owners:
        owners = {(pid, ticks) for pid, ticks in owners if fingerprint(pid) == ticks}
        if not owners:
            break
        if time.monotonic() >= deadline:
            print('[hermes] gateway shutdown wait exhausted; final s6 sweep follows', flush=True)
            break
        time.sleep(.1)


if __name__ == '__main__':
    main()
