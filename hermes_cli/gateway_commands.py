"""Gateway command dispatch within lifecycle restart ownership."""
import sys
from contextvars import ContextVar
from pathlib import Path
_GATEWAY_RESTART_LEASE_HELD = ContextVar("gateway_restart_lease_held", default=False)

def _gateway_command_inner(args):
    from hermes_cli.gateway import (
        _GATEWAY_SUBCOMMANDS, print_error, print_warning, get_hermes_home,
    )
    subcmd = getattr(args, "gateway_command", None)
    if subcmd == "restart" and not _GATEWAY_RESTART_LEASE_HELD.get():
        from tools.process_registry import _is_supervised_gateway_process

        if _is_supervised_gateway_process():
            print_error(
                "Refusing to restart the gateway from inside the gateway process.\n"
                "This command was blocked to prevent restart loops.\n"
                "Use `hermes gateway restart` from a shell outside the running gateway."
            )
            sys.exit(1)

        from hermes_cli.lifecycle_coordination import (
            LifecycleCoordinationBlocked,
            acquire_cli_lifecycle_transaction,
            lifecycle_coordination_available,
        )

        try:
            lease = None
            if lifecycle_coordination_available():
                lease = acquire_cli_lifecycle_transaction(
                    home=get_hermes_home(),
                    repo_root=Path(__file__).resolve().parents[1],
                    purpose="gateway-restart",
                    operation="hermes-gateway-restart",
                )
            else:
                print_warning(
                    "Lifecycle coordination is unavailable on this platform; "
                    "continuing with the existing restart path."
                )
        except LifecycleCoordinationBlocked as exc:
            print_error(
                f"Gateway restart blocked by lifecycle coordination: {exc}\n"
                "Inspect ownership with `hermes lifecycle-lease inspect`."
            )
            sys.exit(2)

        token = _GATEWAY_RESTART_LEASE_HELD.set(True)
        primary: BaseException | None = None
        try:
            return _gateway_command_inner(args)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            _GATEWAY_RESTART_LEASE_HELD.reset(token)
            try:
                if lease is not None:
                    lease.release()
            except BaseException as release_exc:
                if primary is not None:
                    raise release_exc from primary
                raise

    handler = _GATEWAY_SUBCOMMANDS.get(getattr(args, "gateway_command", None))
    if handler is not None:
        handler(args)
