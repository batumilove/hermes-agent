"""Gateway control socket lifecycle and loop-owned live status publication."""

async def _start_gateway_start_control_socket(runner):
    """Start the gateway control socket (identify/status/pause-for-update); None when unavailable."""
    from gateway.run import asyncio, threading, logger, _build_live_control_status
    import concurrent.futures
    import os
    import atexit
    _control_server = None
    try:
        # Started immediately after the PID-file claim: winning that O_EXCL race is the moment this process
        # becomes the authoritative gateway for its HERMES_HOME, so from here on "does a socket answer?" is
        # a truthful liveness/identity query for updater and fleet consumers. Strictly non-fatal: a bind
        # failure only means consumers fall back to the process-scan/state-file layer, exactly as before
        # this feature. See #92091.
        from gateway.control_socket import GatewayControlServer
        from gateway.run_profile_reconcile import (
            migrate_profile_identity_verb, purge_profile_identity_verb,
            unserve_profile_verb, serve_profile_verb,
        )
        from gateway.run_plugin_rewire import reload_plugins_verb
        # pause-for-update: the updater asks us to drain + exit (freeing venv handles) vs. a tree-kill
        # (same path as SIGUSR1). Handler runs on the socket executor thread, so marshal onto the loop.
        # pause-for-update (#92091 step 2): the updater asks this gateway to drain in-flight turns and exit
        # cleanly — releasing every venv file handle — instead of being tree-killed mid-turn. Same drain
        # path as SIGUSR1/service restarts (request_restart(via_service=True)); the updater (or the service
        # manager) relaunches after the code swap.
        _main_loop = asyncio.get_running_loop()

        def _pause_for_update_handler() -> dict:
            try:
                from hermes_cli.gateway import _get_restart_drain_timeout
                _drain = float(_get_restart_drain_timeout())
            except Exception:
                _drain = 30.0
            accepted_box: list[bool] = []
            _done = threading.Event()

            def _request() -> None:
                try:
                    accepted_box.append(runner.request_restart(detached=False, via_service=True))
                finally:
                    _done.set()

            _main_loop.call_soon_threadsafe(_request)
            _done.wait(timeout=5.0)
            accepted = bool(accepted_box and accepted_box[0])
            return {
                "pausing": accepted, "already_stopping": not accepted,
                "pid": os.getpid(), "drain_timeout": _drain}

        def _rescan_profiles_handler() -> dict:
            """``hermes profile create/delete`` asks the multiplexer to reconcile ``profiles/`` now
            (the watcher also rescans periodically). Runs on the socket executor: marshal onto the loop
            and wait briefly so the caller learns whether the profile is served."""
            if not getattr(runner.config, "multiplex_profiles", False):
                return {"multiplex": False, "served_profiles": runner.served_profile_names()}
            future = asyncio.run_coroutine_threadsafe(
                runner.reconcile_served_profiles(reason="control-socket"), _main_loop)
            try:
                # Bounded: a token-less create reconciles in milliseconds; a credential-add whose adapter
                # connect outlasts this keeps running and the caller sees ``pending`` (not an error).
                return {"multiplex": True, **future.result(timeout=5.0)}
            except concurrent.futures.TimeoutError:
                return {"multiplex": True, "pending": True, "served_profiles": runner.served_profile_names()}

        def _live_status_handler():
            from gateway.control_socket import build_status_payload
            # This handler runs on the socket executor: read disk here, then
            # snapshot only loop-owned runtime counts on the gateway loop.
            payload = build_status_payload()
            async def _snapshot():
                return _build_live_control_status(runner, payload)
            future = asyncio.run_coroutine_threadsafe(_snapshot(), _main_loop)
            return future.result(timeout=5.0)

        _control_server = GatewayControlServer(
            verb_handlers={"status": _live_status_handler, "pause-for-update": _pause_for_update_handler,
                           "rescan-profiles": _rescan_profiles_handler,
                           "unserve-profile": unserve_profile_verb(runner),
                           "serve-profile": serve_profile_verb(runner),
                           "migrate-profile-identity": migrate_profile_identity_verb(runner),
                           "purge-profile-identity": purge_profile_identity_verb(runner),
                           # A plugin installed/enabled by another process loads now and re-wires the
                           # live adapters' handlers (#87770); tools/prompt still wait for the next session.
                           "reload-plugins": reload_plugins_verb(runner, _main_loop)})
        if not await _control_server.start():
            _control_server = None
        else:
            atexit.register(_control_server.cleanup_files)
    except Exception as _cs_exc:
        logger.debug("Control socket startup failed (non-fatal): %s", _cs_exc)
        _control_server = None
    return _control_server
