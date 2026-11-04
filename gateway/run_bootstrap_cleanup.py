"""Final identity handoff after bootstrap has drained the runtime writers."""

import logging

logger = logging.getLogger("gateway.run")


async def finalize_runtime_identity(control_server):
    from gateway.status import remove_pid_file, release_gateway_runtime_lock

    try:
        if control_server is not None:
            await control_server.stop()
    finally:
        # Cleanup failures and cancellation must not skip the independent
        # identity handoff. Preserve the original exception after attempting it.
        try:
            remove_pid_file()
        finally:
            # The aggregate drain is already over. A pending status write or
            # surviving runtime writer keeps ownership until process exit;
            # never spend another ten seconds waiting behind it on the loop.
            if not release_gateway_runtime_lock(timeout=0.0):
                logger.warning("Gateway identity retained for outstanding writers until process exit")
