"""The native desktop packages a release builds, defined once.

A target is an (electron platform, arch) pair. Every other spelling derives from
it: the build leg and handoff name ``<platform>-<arch>``, and the manifest row
platform (``macos`` / ``windows``). The fork builds both macOS architectures; Windows is retained only as historical data.
"""
from __future__ import annotations

TARGETS = (("darwin", "arm64"), ("darwin", "x64"))
MANIFEST_PLATFORM = {"darwin": "macos", "win32": "windows"}
# Manifest rows name the platform as macos/windows.
MANIFEST_TARGETS = tuple((MANIFEST_PLATFORM[platform], arch) for platform, arch in TARGETS)
# One staged handoff per target, plus the universal Windows bundle assembled from both arches.
NATIVE_LEGS = tuple(f"{platform}-{arch}" for platform, arch in TARGETS)
