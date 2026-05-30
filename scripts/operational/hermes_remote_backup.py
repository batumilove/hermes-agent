#!/usr/bin/env python3
"""Hermes remote backup to Proxmox storage.

Creates a streamable tar archive of selected Hermes runtime state, using
SQLite's backup API for live DB files, compresses with zstd, transfers over SSH,
writes a remote manifest/checksum, and prunes old archives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REMOTE = "root@proxmox01"
DEFAULT_REMOTE_DIR = "/mnt/agent-vault-backups/hermes-backups/hermes-vm"
DEFAULT_MIN_AVAIL_GB = 20
DEFAULT_KEEP_DAILY = 14

# Keep this conservative: runtime state, not reproducible code/dependency caches.
EXCLUDED_DIR_NAMES = {
    "hermes-agent",      # code repo, re-clone/update instead
    "node",              # bundled/generated node runtime; large, reproducible
    "node_modules",
    ".git",
    "__pycache__",
    "backups",           # avoid nested backups
    "checkpoints",        # local trajectory cache
    "cache",
    "audio_cache",        # generated media cache
    ".cache",
}
EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo", ".db-wal", ".db-shm", ".db-journal")
EXCLUDED_FILE_NAMES = {"gateway.pid", "cron.pid"}


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def remote(remote_host: str, command: str, *, check: bool = True) -> subprocess.CompletedProcess:
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", remote_host, command], check=check)


def should_exclude(rel: Path, extra_excluded_dirs: set[str] | None = None) -> bool:
    excluded_dirs = EXCLUDED_DIR_NAMES | (extra_excluded_dirs or set())
    if any(part in excluded_dirs for part in rel.parts):
        return True
    if rel.name in EXCLUDED_FILE_NAMES:
        return True
    if rel.name.endswith(EXCLUDED_FILE_SUFFIXES):
        return True
    return False


def safe_copy_db(src: Path, dst: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        out = sqlite3.connect(str(dst))
        conn.backup(out)
        out.close()
        conn.close()
        return True
    except Exception as exc:
        log(f"WARN sqlite backup failed for {src}: {exc}; falling back to raw copy")
        try:
            shutil.copy2(src, dst)
            return True
        except Exception as exc2:
            log(f"ERROR raw DB copy failed for {src}: {exc2}")
            return False


def iter_files(home: Path, extra_excluded_dirs: set[str] | None = None):
    excluded_dirs = EXCLUDED_DIR_NAMES | (extra_excluded_dirs or set())
    for dirpath, dirnames, filenames in os.walk(home, followlinks=False):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(home)
        dirnames[:] = [d for d in dirnames if d not in excluded_dirs and not should_exclude(rel_dir / d, extra_excluded_dirs)]
        for fname in filenames:
            p = dp / fname
            rel = p.relative_to(home)
            if should_exclude(rel, extra_excluded_dirs):
                continue
            if p.is_symlink() or not p.is_file():
                continue
            yield p, rel


def create_quick_snapshot(label: str) -> str:
    p = subprocess.run(["hermes", "backup", "--quick", "--label", label], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if p.returncode != 0:
        raise RuntimeError(f"quick snapshot failed: {p.stdout.strip()}")
    return p.stdout.strip()


def preflight(remote_host: str, remote_dir: str, min_avail_gb: int) -> tuple[bool, str]:
    cmd = (
        "set -euo pipefail; "
        f"test -d {shq(remote_dir)}; test -w {shq(remote_dir)}; "
        f"avail=$(df -Pk {shq(remote_dir)} | awk 'NR==2{{print $4}}'); "
        f"need=$(( {min_avail_gb} * 1024 * 1024 )); "
        "if [ \"$avail\" -lt \"$need\" ]; then echo LOW_SPACE:$avail; exit 42; fi; "
        "echo OK:$avail"
    )
    p = remote(remote_host, cmd, check=False)
    out = (p.stdout + p.stderr).decode(errors="replace").strip()
    if p.returncode != 0:
        return False, out or f"ssh/preflight failed with exit {p.returncode}"
    return True, out


def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def min_avail_gb_to_bytes(gb: int) -> int:
    return gb * 1024 * 1024 * 1024


def remote_sha256(remote_host: str, path: str) -> str:
    p = remote(remote_host, f"sha256sum {shq(path)} | awk '{{print $1}}'")
    return p.stdout.decode().strip()


def prune_remote(remote_host: str, remote_dir: str, keep_daily: int) -> str:
    # Simple retention: keep newest N archives. Weekly/monthly policy can be layered later
    # once the storage target is known and capacity is measured.
    cmd = (
        "set -euo pipefail; cd " + shq(remote_dir) + "; "
        "ls -1t hermes-vm-*.tar.zst 2>/dev/null | awk 'NR>" + str(keep_daily) + "' | "
        "while IFS= read -r f; do rm -f -- \"$f\" \"$f.sha256\" \"$f.manifest.json\"; echo \"deleted $f\"; done"
    )
    p = remote(remote_host, cmd, check=False)
    return (p.stdout + p.stderr).decode(errors="replace").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".hermes"))
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    ap.add_argument("--min-avail-gb", type=int, default=DEFAULT_MIN_AVAIL_GB)
    ap.add_argument("--keep-daily", type=int, default=DEFAULT_KEEP_DAILY)
    ap.add_argument("--label", default="scheduled")
    ap.add_argument("--no-quick", action="store_true")
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument(
        "--tmpdir",
        default=os.environ.get("HERMES_BACKUP_TMPDIR", str(Path.home() / ".hermes" / "tmp")),
        help="Local scratch space for SQLite-consistent DB snapshots. Defaults to ~/.hermes/tmp, not /tmp.",
    )
    ap.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=0,
        help="Abort before uploading if estimated runtime would exceed this budget. 0 disables.",
    )
    ap.add_argument(
        "--skip-dir",
        action="append",
        default=[],
        help="Additional top-level or nested directory name to exclude. May be repeated.",
    )
    args = ap.parse_args()

    home = Path(args.home).expanduser().resolve()
    extra_excluded_dirs = {d.strip().strip("/") for d in args.skip_dir if d.strip().strip("/")}
    if not home.is_dir():
        log(f"ERROR Hermes home not found: {home}")
        return 2

    ok, msg = preflight(args.remote, args.remote_dir, args.min_avail_gb)
    if not ok:
        log("Hermes backup NOT RUN: remote target preflight failed")
        log(f"Remote: {args.remote}:{args.remote_dir}")
        log(f"Reason: {msg}")
        log("Expected fix: mount/create the Proxmox backup HDD/NFS target and create a writable backup directory on that mounted filesystem.")
        return 3
    log(f"Remote preflight OK: {msg}")
    if args.preflight_only:
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "".join(c if c.isalnum() or c in "._-" else "-" for c in args.label)[:40] or "backup"
    base = f"hermes-vm-{ts}-{label}.tar.zst"
    remote_tmp = f"{args.remote_dir}/.{base}.tmp"
    remote_final = f"{args.remote_dir}/{base}"

    if not args.no_quick:
        log("Creating local quick state snapshot...")
        snap_out = create_quick_snapshot(f"pre-remote-{label}")
        log(snap_out.splitlines()[0] if snap_out else "Quick snapshot created")

    file_count = 0
    total_bytes = 0
    db_count = 0
    sha = hashlib.sha256()
    start = time.monotonic()
    runtime_expired = False

    scratch_root = Path(args.tmpdir).expanduser().resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(scratch_root)
    min_scratch = max(512 * 1024 * 1024, min_avail_gb_to_bytes(2))
    if usage.free < min_scratch:
        log(f"ERROR local scratch space too low at {scratch_root}: free={usage.free} bytes")
        return 2

    with tempfile.TemporaryDirectory(prefix="hermes-remote-backup-", dir=str(scratch_root)) as td:
        tmpdir = Path(td)
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", args.remote, f"umask 077; cat > {shq(remote_tmp)}"]
        zstd = subprocess.Popen(["zstd", "-T0", "-10", "-q", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sshp = subprocess.Popen(ssh_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        assert zstd.stdin is not None and zstd.stdout is not None and sshp.stdin is not None

        def pump_compressed() -> None:
            try:
                while True:
                    chunk = zstd.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    sha.update(chunk)
                    sshp.stdin.write(chunk)
            finally:
                try:
                    sshp.stdin.close()
                except BrokenPipeError:
                    pass

        pump_thread = threading.Thread(target=pump_compressed, daemon=True)
        pump_thread.start()

        try:
            with tarfile.open(fileobj=zstd.stdin, mode="w|") as tf:
                manifest = {
                    "created_utc": ts,
                    "source_host": os.uname().nodename,
                    "hermes_home": str(home),
                    "format": "tar.zst",
                    "exclusions": {
                        "dir_names": sorted(EXCLUDED_DIR_NAMES | extra_excluded_dirs),
                        "suffixes": list(EXCLUDED_FILE_SUFFIXES),
                        "file_names": sorted(EXCLUDED_FILE_NAMES),
                    },
                    "files": [],
                    "complete": True,
                    "runtime_budget_seconds": args.max_runtime_seconds or None,
                }
                for src, rel in iter_files(home, extra_excluded_dirs):
                    if args.max_runtime_seconds and (time.monotonic() - start) > args.max_runtime_seconds:
                        runtime_expired = True
                        log(f"WARN runtime budget exceeded after {file_count} files; finalizing partial archive")
                        break
                    add_src = src
                    cleanup = None
                    try:
                        st = src.stat()
                        if src.suffix == ".db":
                            db_count += 1
                            snap = tmpdir / ("db-" + str(db_count) + ".db")
                            if not safe_copy_db(src, snap):
                                continue
                            add_src = snap
                            cleanup = snap
                        info = tf.gettarinfo(str(add_src), arcname=rel.as_posix())
                        # Keep secrets owner-readable inside archive; extraction target should also be private.
                        if rel.name in {".env", "auth.json", "state.db"}:
                            info.mode = 0o600
                        with open(add_src, "rb") as f:
                            tf.addfile(info, f)
                        file_count += 1
                        total_bytes += st.st_size
                        manifest["files"].append({"path": rel.as_posix(), "size": st.st_size, "db_snapshot": src.suffix == ".db"})
                        if cleanup:
                            cleanup.unlink(missing_ok=True)
                    except Exception as exc:
                        log(f"WARN skipped {rel}: {exc}")
                manifest["complete"] = not runtime_expired
                meta_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
                ti = tarfile.TarInfo("BACKUP-MANIFEST.json")
                ti.size = len(meta_bytes)
                ti.mtime = int(time.time())
                ti.mode = 0o600
                import io
                tf.addfile(ti, io.BytesIO(meta_bytes))
        finally:
            try:
                zstd.stdin.close()
            except BrokenPipeError:
                pass

        zstd_rc = zstd.wait()
        pump_thread.join(timeout=60)
        ssh_rc = sshp.wait()
        ssh_out = sshp.stdout.read() if sshp.stdout else b""
        ssh_err = sshp.stderr.read() if sshp.stderr else b""
        zstd_err = zstd.stderr.read() if zstd.stderr else b""
        if zstd_rc != 0 or ssh_rc != 0:
            log(f"ERROR transfer failed zstd={zstd_rc} ssh={ssh_rc}: {(zstd_err+ssh_out+ssh_err).decode(errors='replace')}")
            remote(args.remote, f"rm -f {shq(remote_tmp)}", check=False)
            return 4

    digest = sha.hexdigest()
    remote(args.remote, f"mv -f {shq(remote_tmp)} {shq(remote_final)}")
    remote_digest = remote_sha256(args.remote, remote_final)
    if remote_digest != digest:
        log(f"ERROR checksum mismatch local={digest} remote={remote_digest}")
        return 5
    checksum_text = f"{digest}  {base}\n"
    # Send checksum/manifest via ssh stdin safely.
    subprocess.run(["ssh", args.remote, f"umask 077; cat > {shq(remote_final + '.sha256')}"], input=checksum_text.encode(), check=True)
    manifest_text = json.dumps({
        "archive": base,
        "sha256": digest,
        "source_host": os.uname().nodename,
        "created_utc": ts,
        "file_count": file_count,
        "original_bytes": total_bytes,
        "sqlite_databases": db_count,
        "complete": not runtime_expired,
        "runtime_budget_seconds": args.max_runtime_seconds or None,
        "remote": f"{args.remote}:{remote_final}",
        "restore_note": "Copy archive to a restore host, verify sha256, then extract into a fresh/stopped HERMES_HOME with: tar --zstd -xpf archive.tar.zst -C ~/.hermes",
    }, indent=2, sort_keys=True) + "\n"
    subprocess.run(["ssh", args.remote, f"umask 077; cat > {shq(remote_final + '.manifest.json')}"], input=manifest_text.encode(), check=True)

    prune_msg = prune_remote(args.remote, args.remote_dir, args.keep_daily)
    elapsed = time.monotonic() - start
    stat = remote(args.remote, f"du -h {shq(remote_final)} | awk '{{print $1}}' && df -h {shq(args.remote_dir)} | tail -1")
    stat_text = stat.stdout.decode(errors="replace").strip()
    log("Hermes backup OK" if not runtime_expired else "Hermes backup OK (partial due runtime budget)")
    log(f"Archive: {args.remote}:{remote_final}")
    log(f"SHA256: {digest}")
    log(f"Files: {file_count}; DB snapshots: {db_count}; source bytes: {total_bytes}; elapsed: {elapsed:.1f}s")
    log(stat_text)
    if prune_msg:
        log("Retention prune:")
        log(prune_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
