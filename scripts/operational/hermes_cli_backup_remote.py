#!/usr/bin/env python3
"""Create and verify an importable Hermes CLI backup zip off-host.

This complements the tar.zst disaster-recovery runtime archive produced by
hermes_remote_backup.py. The zip created here is the format accepted by
`hermes import`, so it gives us a tested import path in addition to raw
runtime-state restoration.
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
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REMOTE = "root@proxmox01"
DEFAULT_REMOTE_DIR = "/mnt/agent-vault-backups/hermes-backups/hermes-vm/importable"
DEFAULT_MIN_AVAIL_GB = 20
DEFAULT_KEEP = 7
CRITICAL_PATHS = [
    "config.yaml",
    ".env",
    "state.db",
    "lcm.db",
    "response_store.db",
    "kanban.db",
    "cron/jobs.json",
    "gateway_state.json",
    "channel_directory.json",
]


def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def run(cmd: list[str], *, input_bytes: bytes | None = None, env: dict[str, str] | None = None, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, env=env, check=check, timeout=timeout)


def remote(remote_host: str, command: str, *, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", remote_host, command], check=check, timeout=timeout)


def log(msg: str) -> None:
    print(msg, flush=True)


def preflight(remote_host: str, remote_dir: str, min_avail_gb: int) -> tuple[bool, str]:
    cmd = (
        "set -euo pipefail; "
        f"mkdir -p {shq(remote_dir)}; test -w {shq(remote_dir)}; "
        f"avail=$(df -Pk {shq(remote_dir)} | awk 'NR==2{{print $4}}'); "
        f"need=$(( {min_avail_gb} * 1024 * 1024 )); "
        "if [ \"$avail\" -lt \"$need\" ]; then echo LOW_SPACE:$avail; exit 42; fi; "
        f"findmnt -T {shq(remote_dir)} >/dev/null; "
        "echo OK:$avail"
    )
    p = remote(remote_host, cmd, check=False)
    out = (p.stdout + p.stderr).decode(errors="replace").strip()
    if p.returncode != 0:
        return False, out or f"ssh/preflight failed with exit {p.returncode}"
    return True, out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def zip_manifest(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"zip integrity failed at {bad}")
        names = set(zf.namelist())
    return {
        "file_count": len(names),
        "critical": {p: p in names for p in CRITICAL_PATHS},
        "has_sessions": any(n.startswith("sessions/") for n in names),
        "has_skills": any(n.startswith("skills/") for n in names),
    }


def sqlite_integrity_checks(restored_home: Path, per_db_timeout: int = 120) -> dict[str, str]:
    results: dict[str, str] = {}
    for rel in ["state.db", "lcm.db", "response_store.db", "kanban.db"]:
        db = restored_home / rel
        if not db.exists():
            results[rel] = "missing"
            continue
        try:
            conn = sqlite3.connect(str(db), timeout=30)
            # Open read-write (temp dir is disposable) so SQLite can create
            # WAL/SHM companions if the DB header still says WAL mode.
            # A read-only open of a WAL-mode DB without its WAL/SHM files
            # produces "disk I/O error".
            # Switch to delete journal mode so WAL sidecars aren't needed.
            conn.execute("PRAGMA journal_mode=delete;")
            conn.execute(f"PRAGMA busy_timeout={per_db_timeout * 1000};")
            row = conn.execute("PRAGMA integrity_check;").fetchone()
            conn.close()
            results[rel] = str(row[0]) if row else "no result"
        except Exception as exc:  # noqa: BLE001 - backup verifier should report exact DB issue
            results[rel] = f"error: {exc}"
    return results


def import_smoke_test(zip_path: Path, hermes_bin: str, timeout: int) -> dict[str, object]:
    """Restore the zip into an isolated HERMES_HOME and verify key files.

    Use Popen with incremental stdout draining. The current Hermes import command
    prints progress every 500 files; if stdout is captured with subprocess.run()
    and never drained, a large import can block on a full pipe and then get
    killed by our timeout. This verifier must test importability, not stdout
    buffering behavior.
    """
    with tempfile.TemporaryDirectory(prefix="hermes-import-smoke-") as td:
        test_home = Path(td) / ".hermes"
        env = os.environ.copy()
        env["HERMES_HOME"] = str(test_home)
        proc = subprocess.Popen(
            [hermes_bin, "import", "--force", str(zip_path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        tail: list[str] = []
        assert proc.stdout is not None
        deadline = time.monotonic() + timeout
        while True:
            line = proc.stdout.readline()
            if line:
                tail.append(line.decode(errors="replace"))
                tail = tail[-80:]
            if proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    tail.append(rest.decode(errors="replace"))
                    tail = tail[-80:]
                break
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait(timeout=10)
                raise RuntimeError(f"hermes import smoke test timed out after {timeout}s:\n{''.join(tail)[-4000:]}")
        if proc.returncode != 0:
            raise RuntimeError(f"hermes import smoke test failed rc={proc.returncode}:\n{''.join(tail)[-4000:]}")
        critical = {p: (test_home / p).exists() for p in CRITICAL_PATHS}
        db_checks = sqlite_integrity_checks(test_home)
        return {
            "returncode": proc.returncode,
            "critical": critical,
            "sqlite_integrity": db_checks,
        }


def upload(remote_host: str, local_path: Path, remote_path: str) -> None:
    with local_path.open("rb") as f:
        p = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", remote_host, f"umask 077; cat > {shq(remote_path)}"],
            stdin=f,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if p.returncode != 0:
        raise RuntimeError((p.stdout + p.stderr).decode(errors="replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".hermes"))
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    ap.add_argument("--min-avail-gb", type=int, default=DEFAULT_MIN_AVAIL_GB)
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--label", default="scheduled")
    ap.add_argument("--tmpdir", default=os.environ.get("HERMES_BACKUP_TMPDIR", str(Path.home() / ".hermes" / "tmp")))
    ap.add_argument("--hermes-bin", default=shutil.which("hermes") or "hermes")
    ap.add_argument("--import-timeout", type=int, default=180)
    ap.add_argument("--preflight-only", action="store_true")
    args = ap.parse_args()

    ok, msg = preflight(args.remote, args.remote_dir, args.min_avail_gb)
    if not ok:
        log("Hermes importable backup NOT RUN: remote target preflight failed")
        log(f"Remote: {args.remote}:{args.remote_dir}")
        log(f"Reason: {msg}")
        return 3
    log(f"Remote preflight OK: {msg}")
    if args.preflight_only:
        return 0

    home = Path(args.home).expanduser().resolve()
    if not home.is_dir():
        log(f"ERROR Hermes home not found: {home}")
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "".join(c if c.isalnum() or c in "._-" else "-" for c in args.label)[:40] or "backup"
    base = f"hermes-cli-{ts}-{label}.zip"
    scratch_root = Path(args.tmpdir).expanduser().resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)

    # Checkpoint all WAL-mode SQLite DBs so the main .db files are
    # self-contained and the import smoke test won't hit "disk I/O error"
    # when opening them without the WAL/SHM sidecars.
    for rel in ["state.db", "lcm.db", "response_store.db", "kanban.db"]:
        db_path = home / rel
        if db_path.is_file():
            try:
                conn = sqlite3.connect(str(db_path), timeout=30)
                jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
                if jm.lower() == "wal":
                    conn.execute("PRAGMA wal_checkpoint(FULL)")
                conn.close()
            except Exception as exc:
                log(f"WARNING WAL checkpoint failed for {rel}: {exc}")

    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hermes-cli-backup-", dir=str(scratch_root)) as td:
        local_zip = Path(td) / base
        env = os.environ.copy()
        env["HERMES_HOME"] = str(home)
        log("Creating Hermes CLI backup zip...")
        p = run([args.hermes_bin, "backup", "--output", str(local_zip)], env=env, check=False, timeout=600)
        out = (p.stdout + p.stderr).decode(errors="replace")
        if p.returncode != 0 or not local_zip.is_file():
            log(out[-4000:])
            log(f"ERROR hermes backup failed rc={p.returncode}")
            return 4

        local_manifest = zip_manifest(local_zip)
        zip_size = local_zip.stat().st_size

        # Check disk space before import smoke test. The zip and the
        # uncompressed extraction coexist momentarily. Read the actual
        # uncompressed size from the zip central directory rather than
        # guessing a compression-ratio multiplier.
        import_result: dict[str, object] | None = None
        stat = os.statvfs(str(scratch_root))
        avail = stat.f_bavail * stat.f_frsize
        with zipfile.ZipFile(local_zip) as _zf:
            uncompressed = sum(i.file_size for i in _zf.infolist())
        need = zip_size + uncompressed + 512 * 1024 * 1024  # zip + extract + 512MB headroom
        if avail >= need:
            import_result = import_smoke_test(local_zip, args.hermes_bin, args.import_timeout)
        else:
            log(f"WARNING skipping import smoke test: {avail // (1024*1024)}MB free < {need // (1024*1024)}MB needed (zip {zip_size//1024//1024}MB + uncompressed {uncompressed//1024//1024}MB + 512MB headroom)")
            import_result = {"skipped": True, "reason": "insufficient disk space for smoke test"}

        digest = sha256_file(local_zip)
        size = zip_size

        remote_tmp = f"{args.remote_dir}/.{base}.tmp"
        remote_final = f"{args.remote_dir}/{base}"
        log("Uploading importable zip...")
        try:
            upload(args.remote, local_zip, remote_tmp)
            remote(args.remote, f"mv -f {shq(remote_tmp)} {shq(remote_final)}")
            rsha = remote(args.remote, f"cd {shq(args.remote_dir)}; sha256sum {shq(base)} | awk '{{print $1}}'").stdout.decode().strip()
            if rsha != digest:
                raise RuntimeError(f"remote checksum mismatch local={digest} remote={rsha}")
            remote(args.remote, f"cd {shq(args.remote_dir)}; unzip -tq {shq(base)}")
            checksum_text = f"{digest}  {base}\n".encode()
            subprocess.run(["ssh", args.remote, f"umask 077; cat > {shq(remote_final + '.sha256')}"], input=checksum_text, check=True)
            manifest = {
                "archive": base,
                "sha256": digest,
                "size_bytes": size,
                "created_utc": ts,
                "source_host": os.uname().nodename,
                "remote": f"{args.remote}:{remote_final}",
                "format": "hermes-cli-zip",
                "restore_command": f"hermes import {base}",
                "zip_manifest": local_manifest,
                "import_smoke_test": import_result,
            }
            manifest_text = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            subprocess.run(["ssh", args.remote, f"umask 077; cat > {shq(remote_final + '.manifest.json')}"], input=manifest_text, check=True)
            prune_cmd = (
                "set -euo pipefail; cd " + shq(args.remote_dir) + "; "
                "ls -1t hermes-cli-*.zip 2>/dev/null | awk 'NR>" + str(args.keep) + "' | "
                "while IFS= read -r f; do rm -f -- \"$f\" \"$f.sha256\" \"$f.manifest.json\"; echo \"deleted $f\"; done"
            )
            prune = remote(args.remote, prune_cmd, check=False).stdout.decode(errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            remote(args.remote, f"rm -f {shq(remote_tmp)}", check=False)
            log(f"ERROR upload/remote verification failed: {exc}")
            return 5

    elapsed = time.monotonic() - start
    skipped_smoke = bool(import_result.get("skipped"))
    if not skipped_smoke:
        ok_critical = all(local_manifest["critical"].values()) and all(import_result["critical"].values())
        ok_dbs = all(v == "ok" for v in import_result["sqlite_integrity"].values() if v != "missing")
        if not ok_critical or not ok_dbs:
            log("ERROR importable backup verification failed")
            log(json.dumps({"zip_manifest": local_manifest, "import_result": import_result}, indent=2, sort_keys=True))
            return 6
    else:
        ok_critical = all(local_manifest["critical"].values())
        if not ok_critical:
            log("ERROR importable backup zip manifest verification failed (smoke test skipped)")
            log(json.dumps({"zip_manifest": local_manifest, "import_result": import_result}, indent=2, sort_keys=True))
            return 6

    if skipped_smoke:
        log("Hermes importable backup OK (smoke test skipped — disk space)")
    else:
        log("Hermes importable backup OK")
    log(f"Archive: {args.remote}:{args.remote_dir}/{base}")
    log(f"SHA256: {digest}")
    log(f"Files: {local_manifest['file_count']}; size: {size}; elapsed: {elapsed:.1f}s")
    if prune:
        log("Retention prune:")
        log(prune)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
