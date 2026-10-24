"""Capacity admission only; not dependency or fleet transaction preparation."""
from __future__ import annotations
import os
import shutil
from pathlib import Path


def storage_refusal(paths, *, required_bytes=512 * 1024 * 1024, required_inodes=4096):
    """Fixed headroom on supplied filesystems, not complete build/restore sizing."""
    for path in paths:
        parent = Path(path).resolve()
        while not parent.exists():
            parent = parent.parent
        try:
            free_bytes = shutil.disk_usage(parent).free
            # Windows exposes free bytes but has no POSIX inode quota API.
            stat = os.statvfs(parent) if hasattr(os, "statvfs") else None
        except OSError as exc:
            return f"cannot inspect update storage {parent}: {exc}"
        if free_bytes < required_bytes:
            return f"insufficient free space for update at {parent} (need {required_bytes} bytes)"
        if stat is not None and stat.f_files and stat.f_favail < required_inodes:
            return f"insufficient free inodes for update at {parent} (need {required_inodes})"
    return None
