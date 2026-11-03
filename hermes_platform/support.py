"""The fork's supported control hosts; remote execution targets are independent."""

from __future__ import annotations

import platform
import sysconfig

from hermes_platform.host.facts import native_arch, os_family
from hermes_platform.host.runtime import is_termux

SUPPORT_MESSAGE = (
    "This fork supports standard Linux and macOS on x86_64 and ARM64 only "
    "(including Linux under WSL2). Native Windows and Android/Termux are unsupported."
)


def supports_host(*, system: str, arch: str, android: bool = False) -> bool:
    """Evaluate host facts without depending on the interpreter's current OS."""
    return system in {"linux", "darwin"} and arch in {"amd64", "arm64"} and not android


def require_supported_host() -> None:
    """Refuse unsupported hosts before application startup changes user state."""
    if not supports_host(
        system=os_family(), arch=native_arch(),
        android=(is_termux() or bool(sysconfig.get_config_var("ANDROID_API_LEVEL"))
                 or "android" in platform.release().lower()),
    ):
        raise SystemExit(SUPPORT_MESSAGE)
