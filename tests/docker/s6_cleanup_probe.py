"""Container-only deterministic post-drain fault injection (no network/platforms)."""
import asyncio
import os
from pathlib import Path

from gateway.run import GatewayRunner, start_gateway, _exit_after_graceful_shutdown

original = GatewayRunner._finalize_shutdown_agents

async def delayed(self, active_agents):
    Path('/opt/data/post-drain-entered').touch()
    if os.environ.get('PROBE_STUCK'):
        # Deliberately block the event loop and ignore further TERM: neither
        # aggregate timeout nor the async watchdog can end this process.
        import signal
        import time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        # Freeze every thread too: the independent thread watchdog otherwise
        # exits at ~60s and never exercises the s6 fallback ceiling.
        os.kill(os.getpid(), signal.SIGSTOP)
        while True:
            time.sleep(1)
    await asyncio.sleep(float(os.environ.get('PROBE_CLEANUP_DELAY', '5')))
    return await original(self, active_agents)

GatewayRunner._finalize_shutdown_agents = delayed
try:
    code = 0 if asyncio.run(start_gateway()) else 1
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
_exit_after_graceful_shutdown(code)
