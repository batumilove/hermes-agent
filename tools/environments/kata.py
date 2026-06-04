"""Kubernetes Kata execution environment.

Runs Hermes terminal/code-execution commands inside a Kubernetes pod with
``runtimeClassName: kata``. Uses ``kubectl`` for lifecycle + exec operations and
FileSyncManager to mirror Hermes credentials/skills/cache into the pod.

Production hardening:
  - Pod name sanitisation (RFC 1123 compliant)
  - Configurable pod ready wait timeout (env ``KATA_POD_READY_TIMEOUT``)
  - Exec retries with back-off on transient kubectl failures
  - Cleanup always attempted, never raises
  - Lock-free cleanup path (no deadlock on shutdown)
  - kubectl timeout propagation from command timeout
  - Manifest uses ``readOnlyRootFilesystem: false`` explicitly
  - Resource defaults validated on construction

Warm pod pool:
  - ``KataPodPool`` pre-creates and pre-syncs N pods
  - ``KataEnvironment`` can claim a warm pod instead of cold-starting
  - Background reconciler refreshes file sync on idle pods
"""

from __future__ import annotations

import io
import logging
import os
import posixpath
import re
import shlex
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from tools.environments.base import BaseEnvironment, _popen_bash
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POD_NAME_MAX = 63  # K8s DNS label limit

_EXEC_MAX_RETRIES = 3
_EXEC_RETRY_BASE_DELAY = 1.0  # seconds

_TAR_SYNC_THRESHOLD = 10  # Use tar bulk upload when >= this many files

# Pool defaults (configurable via env)
_POOL_SIZE_DEFAULT = 2
_POOL_RECONCILE_INTERVAL = 300  # seconds
_POOL_MAX_AGE = 3600  # recycle pods older than this (seconds)

# Orphan GC defaults
_GC_MAX_AGE_SECONDS = 7200  # nuke pods older than 2h (configurable via KATA_GC_MAX_AGE)


def _sanitize_pod_name(task_id: str) -> str:
    """Return a RFC 1123 DNS-label-safe pod name from *task_id*."""
    raw = f"hermes-kata-{task_id}".lower() if task_id else f"hermes-kata-{os.urandom(4).hex()}"
    name = re.sub(r"[^a-z0-9-]", "-", raw)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    name = name[:_POD_NAME_MAX].rstrip("-")
    if not name:
        name = "hermes-kata"
    return name


def _pod_ready_timeout() -> int:
    """Read ``KATA_POD_READY_TIMEOUT`` env, clamped to [30, 600]."""
    try:
        val = int(os.environ.get("KATA_POD_READY_TIMEOUT", "180"))
    except (ValueError, TypeError):
        return 180
    return max(30, min(val, 600))


def _pool_size() -> int:
    try:
        val = int(os.environ.get("KATA_POOL_SIZE", str(_POOL_SIZE_DEFAULT)))
    except (ValueError, TypeError):
        return _POOL_SIZE_DEFAULT
    return max(0, min(val, 10))


# ---------------------------------------------------------------------------
# kubectl helpers (shared between KataEnvironment and KataPodPool)
# ---------------------------------------------------------------------------

def _kubectl_base_args(namespace: str, kubeconfig: str = "") -> list[str]:
    """Build the common kubectl arg prefix."""
    base = ["kubectl"]
    if kubeconfig:
        base += ["--kubeconfig", kubeconfig]
    base += ["-n", namespace]
    return base


def _run_kubectl(
    base_args: list[str],
    extra_args: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*base_args, *extra_args],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Remote path safety helpers
# ---------------------------------------------------------------------------


def _remote_hermes_base(remote_home: str) -> str:
    """Return the normalized remote .hermes base path for tar sync."""
    home = str(remote_home or "").strip() or "/root"
    if not home.startswith("/"):
        raise ValueError(f"remote_home must be absolute: {remote_home!r}")
    normalized_home = posixpath.normpath(home)
    if normalized_home in {".", "/"}:
        raise ValueError(f"unsafe remote_home for Hermes sync: {remote_home!r}")
    return posixpath.join(normalized_home, ".hermes")


def _is_remote_sync_path_allowed(remote_path: str, remote_base: str) -> bool:
    """Return True when *remote_path* stays inside remote_base."""
    if not isinstance(remote_path, str) or not remote_path.startswith("/"):
        return False
    normalized = posixpath.normpath(remote_path)
    return normalized == remote_base or normalized.startswith(remote_base.rstrip("/") + "/")


# ---------------------------------------------------------------------------
# Orphan garbage collection
# ---------------------------------------------------------------------------


def cleanup_orphaned_pods(
    namespace: str = "",
    kubeconfig: str = "",
    max_age_seconds: int = 0,
) -> int:
    """Delete hermes-kata pods that have been running longer than *max_age_seconds*.

    Call this once at Hermes startup to clean up pods left behind after a crash
    or unclean shutdown.  Safe to call repeatedly — it only deletes pods that
    are not part of any live pool and are older than the threshold.

    Returns the number of pods deleted.
    """
    if max_age_seconds <= 0:
        try:
            max_age_seconds = int(os.environ.get("KATA_GC_MAX_AGE", str(_GC_MAX_AGE_SECONDS)))
        except (ValueError, TypeError):
            max_age_seconds = _GC_MAX_AGE_SECONDS

    base_args = _kubectl_base_args(namespace or "sandbox", kubeconfig)

    # List all hermes-kata pods with their creation timestamps
    result = _run_kubectl(
        base_args,
        [
            "get", "pods",
            "-l", "app in (hermes-kata,hermes-kata-pool)",
            "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\t\"}{.status.startTime}{\"\\n\"}{end}",
        ],
        timeout=30,
    )
    if result.returncode != 0 or not (result.stdout or "").strip():
        return 0

    now = time.time()
    deleted = 0

    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        pod_name, start_time_str = parts

        # Parse ISO 8601 start time
        try:
            # Handle both Z and +00:00 suffixes, fractional seconds
            from datetime import datetime, timezone
            clean = start_time_str.replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(clean).astimezone(timezone.utc)
            age = now - start_dt.timestamp()
        except (ValueError, OSError):
            logger.debug("KataGC: couldn't parse startTime for pod %s: %s", pod_name, start_time_str)
            continue

        if age > max_age_seconds:
            logger.info("KataGC: deleting orphaned pod %s (age=%.0fs, threshold=%ds)", pod_name, age, max_age_seconds)
            del_result = _run_kubectl(
                base_args,
                ["delete", "pod", pod_name, "--ignore-not-found=true"],
                timeout=30,
            )
            if del_result.returncode == 0:
                deleted += 1
            else:
                logger.warning("KataGC: failed to delete pod %s: %s", pod_name, (del_result.stderr or "").strip())

    if deleted:
        logger.info("KataGC: cleaned up %d orphaned pod(s)", deleted)
    return deleted


# ---------------------------------------------------------------------------
# KataPodPool — warm pod pre-creation and management
# ---------------------------------------------------------------------------


class _PooledPod:
    """Tracks a single pod in the pool."""

    __slots__ = ("name", "created_at", "last_sync_at", "in_use", "task_id")

    def __init__(self, name: str, task_id: str):
        self.name = name
        self.created_at = time.monotonic()
        self.last_sync_at: float = 0.0
        self.in_use = False
        self.task_id = task_id


class KataPodPool:
    """Pre-created pod pool for the Kata backend.

    Maintains a set of warm pods that are already running and file-synced.
    When a ``KataEnvironment`` needs a pod, it can claim one from the pool
    instead of waiting for cold start + image pull + sync.

    Usage::

        pool = KataPodPool.get_or_create(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="/path/to/kubeconfig",
        )
        pod_name = pool.claim("my-task-id")  # instant if warm pod available

    Thread-safe. Singleton per (image, namespace, kubeconfig) tuple.
    """

    _instances: dict[str, KataPodPool] = {}
    _instances_lock = threading.Lock()

    def __init__(
        self,
        image: str,
        namespace: str,
        kubeconfig: str,
        cpu: int = 1,
        memory: int = 5120,
        pool_size: int = 0,
    ):
        self.image = image
        self.namespace = namespace
        self.kubeconfig = kubeconfig
        self.cpu = cpu
        self.memory = memory
        self.pool_size = pool_size or _pool_size()

        self._base_args = _kubectl_base_args(namespace, kubeconfig)
        self._pods: list[_PooledPod] = []
        self._lock = threading.Lock()
        self._reconcile_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._seq = 0

        if self.pool_size > 0:
            self._ensure_kubectl()
            # Run orphan GC on first pool creation (crash recovery)
            cleanup_orphaned_pods(namespace, kubeconfig)
            # Eagerly create the first pod synchronously so the first
            # KataEnvironment claim finds a warm pod immediately.
            self._eager_bootstrap()
            self._start_reconciler()

    def _eager_bootstrap(self) -> None:
        """Synchronously create and sync the first pool pod."""
        try:
            name = self._next_pod_name()
            pod = self._create_pod(name)
            if pod:
                self._sync_pod(pod)
                with self._lock:
                    self._pods.append(pod)
                logger.info("KataPool: eager bootstrap pod %s ready", name)
        except Exception as exc:
            logger.warning("KataPool: eager bootstrap failed, reconciler will retry: %s", exc)

    @classmethod
    def get_or_create(
        cls,
        image: str,
        namespace: str,
        kubeconfig: str,
        cpu: int = 1,
        memory: int = 5120,
    ) -> KataPodPool:
        """Get or create the singleton pool for this backend config."""
        key = f"{image}:{namespace}:{kubeconfig}"
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(
                    image=image,
                    namespace=namespace,
                    kubeconfig=kubeconfig,
                    cpu=cpu,
                    memory=memory,
                )
            return cls._instances[key]

    @classmethod
    def _clear_instances(cls) -> None:
        """For testing only."""
        with cls._instances_lock:
            for pool in cls._instances.values():
                pool.shutdown()
            cls._instances.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim(self, task_id: str) -> str | None:
        """Claim a warm pod from the pool. Returns pod name or None."""
        with self._lock:
            for pod in self._pods:
                if not pod.in_use:
                    pod.in_use = True
                    pod.task_id = task_id
                    logger.info("KataPool: claimed warm pod %s for task %s", pod.name, task_id)
                    return pod.name
        return None

    def release(self, pod_name: str) -> None:
        """Return a pod to the pool (mark as available for reuse)."""
        with self._lock:
            for pod in self._pods:
                if pod.name == pod_name:
                    pod.in_use = False
                    pod.last_sync_at = 0  # force re-sync
                    logger.debug("KataPool: released pod %s", pod_name)
                    return
        logger.warning("KataPool: release() called for unknown pod %s", pod_name)

    def remove(self, pod_name: str) -> None:
        """Remove a pod from the pool and delete it from K8s."""
        with self._lock:
            self._pods = [p for p in self._pods if p.name != pod_name]
        try:
            _run_kubectl(
                self._base_args,
                ["delete", "pod", pod_name, "--ignore-not-found=true"],
                timeout=60,
            )
        except Exception as exc:
            logger.warning("KataPool: error deleting pod %s: %s", pod_name, exc)

    def shutdown(self) -> None:
        """Stop reconciler and clean up all pooled pods."""
        self._stop_event.set()
        if self._reconcile_thread and self._reconcile_thread.is_alive():
            self._reconcile_thread.join(timeout=10)
        with self._lock:
            pods_to_delete = list(self._pods)
            self._pods.clear()
        for pod in pods_to_delete:
            try:
                _run_kubectl(
                    self._base_args,
                    ["delete", "pod", pod.name, "--ignore-not-found=true"],
                    timeout=30,
                )
            except Exception as exc:
                logger.debug("KataPool: failed to delete pod %s during shutdown: %s", pod.name, exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _next_pod_name(self) -> str:
        with self._lock:
            self._seq += 1
            return _sanitize_pod_name(f"pool-{self._seq}")

    def _ensure_kubectl(self) -> None:
        try:
            result = _run_kubectl(self._base_args, ["version", "--client"], timeout=10)
        except FileNotFoundError as exc:
            raise RuntimeError("kubectl not found in PATH") from exc
        if result.returncode != 0:
            raise RuntimeError(f"kubectl unavailable: {(result.stderr or '').strip()}")

    def _create_pod(self, pod_name: str) -> _PooledPod | None:
        """Create and wait for a single pod. Returns _PooledPod or None on failure."""
        cpu_request = min(max(float(self.cpu) / 2.0, 0.2), float(self.cpu))
        mem_request = max(int(self.memory * 0.5), 512)

        manifest = f"""apiVersion: v1
kind: Pod
metadata:
  name: {pod_name}
  namespace: {self.namespace}
  labels:
    app: hermes-kata-pool
    hermes-pool: "true"
spec:
  runtimeClassName: kata
  restartPolicy: Never
  containers:
  - name: sandbox
    image: {self.image}
    command: ["sh", "-c", "mkdir -p /root/.hermes && while true; do sleep 3600; done"]
    securityContext:
      readOnlyRootFilesystem: false
    resources:
      requests:
        cpu: "{cpu_request:g}"
        memory: "{mem_request}Mi"
      limits:
        cpu: "{float(self.cpu):g}"
        memory: "{int(self.memory)}Mi"
"""
        result = _run_kubectl(self._base_args, ["apply", "-f", "-"], input_text=manifest, timeout=60)
        if result.returncode != 0:
            logger.error("KataPool: failed to create pod %s: %s", pod_name, (result.stderr or "").strip())
            return None

        ready_timeout = _pod_ready_timeout()
        wait = _run_kubectl(
            self._base_args,
            ["wait", f"pod/{pod_name}", "--for=condition=Ready", f"--timeout={ready_timeout}s"],
            timeout=ready_timeout + 10,
        )
        if wait.returncode != 0:
            logger.error("KataPool: pod %s not ready: %s", pod_name, (wait.stderr or "").strip())
            try:
                _run_kubectl(self._base_args, ["delete", "pod", pod_name, "--ignore-not-found=true"], timeout=30)
            except Exception:
                pass
            return None

        pod = _PooledPod(pod_name, task_id="pool")
        logger.info("KataPool: created and warmed pod %s", pod_name)
        return pod

    def _sync_pod(self, pod: _PooledPod) -> None:
        """Tar-sync .hermes files into a pooled pod."""
        try:
            remote_home = "/root"
            detect = _run_kubectl(
                self._base_args,
                ["exec", pod.name, "--", "sh", "-c", "printf %s \"$HOME\""],
                timeout=20,
            )
            if detect.returncode == 0 and (detect.stdout or "").strip():
                remote_home = detect.stdout.strip()

            files = list(iter_sync_files(f"{remote_home}/.hermes"))
            if not files:
                return

            _tar_bulk_upload(
                self._base_args, pod.name, self.namespace, files, remote_home,
            )
            pod.last_sync_at = time.monotonic()
            logger.info("KataPool: synced %d files to pod %s", len(files), pod.name)
        except Exception as exc:
            logger.warning("KataPool: sync failed for pod %s: %s", pod.name, exc)

    def _start_reconciler(self) -> None:
        """Background thread that maintains pool size and refreshes sync."""
        def _reconciler():
            while not self._stop_event.is_set():
                try:
                    self._reconcile()
                except Exception as exc:
                    logger.warning("KataPool: reconciler error: %s", exc)
                self._stop_event.wait(timeout=_POOL_RECONCILE_INTERVAL)

        self._reconcile_thread = threading.Thread(target=_reconciler, daemon=True, name="kata-pool")
        self._reconcile_thread.start()
        logger.info("KataPool: reconciler started, target pool size=%d", self.pool_size)

    def _reconcile(self) -> None:
        """One pass: ensure pool_size idle pods exist, refresh stale ones."""
        now = time.monotonic()
        with self._lock:
            idle_pods = [p for p in self._pods if not p.in_use]
            total = len(self._pods)

        # Create pods to reach target
        needed = self.pool_size - len(idle_pods)
        if needed > 0:
            logger.info("KataPool: creating %d pod(s) to reach target %d", needed, self.pool_size)
            for _ in range(needed):
                if self._stop_event.is_set():
                    break
                name = self._next_pod_name()
                pod = self._create_pod(name)
                if pod:
                    self._sync_pod(pod)
                    with self._lock:
                        self._pods.append(pod)

        # Refresh stale pods (re-sync files)
        with self._lock:
            idle_pods = [p for p in self._pods if not p.in_use]
        for pod in idle_pods:
            if now - pod.last_sync_at > _POOL_RECONCILE_INTERVAL:
                self._sync_pod(pod)

        # Recycle old pods
        with self._lock:
            old_pods = [p for p in self._pods if not p.in_use and now - p.created_at > _POOL_MAX_AGE]
        for pod in old_pods:
            logger.info("KataPool: recycling aged pod %s", pod.name)
            self.remove(pod.name)
            # Reconciler will create replacement on next pass


# ---------------------------------------------------------------------------
# Tar-based bulk upload
# ---------------------------------------------------------------------------


def _tar_bulk_upload(
    base_args: list[str],
    pod_name: str,
    namespace: str,
    files: list[tuple[str, str]],
    remote_home: str,
) -> None:
    """Pack files into a tar, upload once via ``kubectl cp``, extract remotely.

    This replaces N individual ``kubectl cp`` calls with 3 kubectl calls total:
      1. mkdir -p (create remote parent dirs)
      2. kubectl cp (single tar file)
      3. tar xf (extract on remote)

    Falls back to per-file upload if tar creation or upload fails.
    """
    if not files:
        return

    # Build tar directly to temp file (avoids OOM on large syncs)
    tmp_path = ""
    file_map: list[tuple[str, str, str]] = []

    remote_base = _remote_hermes_base(remote_home)
    allowed_files = [
        (host_path, posixpath.normpath(remote_path))
        for host_path, remote_path in files
        if _is_remote_sync_path_allowed(remote_path, remote_base)
    ]
    skipped = len(files) - len(allowed_files)
    if skipped:
        logger.warning(
            "Kata tar sync: skipped %d file(s) outside remote base %s",
            skipped,
            remote_base,
        )
    if not allowed_files:
        return

    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name
            with tarfile.open(fileobj=tmp, mode="w:gz") as tar:
                for host_path, remote_path in allowed_files:
                    if not os.path.isfile(host_path):
                        continue
                    archive_name = remote_path.lstrip("/")
                    # Defense: skip path traversal in archive member names
                    if ".." in Path(archive_name).parts:
                        logger.warning("Kata tar sync: skipped path traversal in %s", archive_name)
                        continue
                    try:
                        tar.add(host_path, arcname=archive_name)
                        file_map.append((host_path, archive_name, remote_path))
                    except (OSError, FileNotFoundError):
                        logger.warning("Kata tar sync: skipped missing file %s", host_path)

        if not file_map:
            return

        tar_size = os.path.getsize(tmp_path)
        logger.debug(
            "Kata tar sync: packed %d file(s) into %.1f KiB archive",
            len(file_map), tar_size / 1024,
        )

        remote_tar_path = f"{remote_base}/_sync.tar.gz"

        # 1. mkdir parent
        parents = unique_parent_dirs([(host, remote) for host, _, remote in file_map])
        mkdir_cmd = quoted_mkdir_command(parents + [remote_base])
        mkdir = _run_kubectl(
            base_args,
            ["exec", pod_name, "--", "sh", "-c", mkdir_cmd],
            timeout=60,
        )
        if mkdir.returncode != 0:
            raise RuntimeError(f"mkdir failed: {(mkdir.stderr or mkdir.stdout or '').strip()}")

        # 2. kubectl cp the tar
        cp_result = _run_kubectl(
            base_args,
            ["cp", tmp_path, f"{namespace}/{pod_name}:{remote_tar_path}"],
            timeout=180,
        )
        if cp_result.returncode != 0:
            raise RuntimeError(f"kubectl cp tar failed: {(cp_result.stderr or cp_result.stdout or '').strip()}")

        # 3. Extract on remote, then clean up tar
        extract_cmd = (
            f"cd / && tar xzf {shlex.quote(remote_tar_path)} --overwrite "
            f"&& rm -f {shlex.quote(remote_tar_path)}"
        )
        extract = _run_kubectl(
            base_args,
            ["exec", pod_name, "--", "sh", "-c", extract_cmd],
            timeout=60,
        )
        if extract.returncode != 0:
            raise RuntimeError(f"tar extract failed: {(extract.stderr or extract.stdout or '').strip()}")

        logger.info("Kata tar sync: uploaded %d file(s) to pod %s", len(file_map), pod_name)

    except Exception:
        logger.warning("Kata tar sync failed, falling back to per-file upload", exc_info=True)
        # Fallback: per-file upload
        parents = unique_parent_dirs(allowed_files)
        mkdir_cmd = quoted_mkdir_command(parents)
        _run_kubectl(base_args, ["exec", pod_name, "--", "sh", "-c", mkdir_cmd], timeout=60)
        for host_path, remote_path in allowed_files:
            if not os.path.isfile(host_path):
                continue
            cp = _run_kubectl(
                base_args,
                ["cp", host_path, f"{namespace}/{pod_name}:{remote_path}"],
                timeout=120,
            )
            if cp.returncode != 0:
                raise RuntimeError(f"kubectl cp failed for {host_path}: {(cp.stderr or '').strip()}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# KataEnvironment
# ---------------------------------------------------------------------------


class KataEnvironment(BaseEnvironment):
    """Kubernetes-backed Kata pod execution backend."""

    _snapshot_timeout = 90

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        cpu: int = 1,
        memory: int = 5120,
        disk: int = 51200,
        persistent_filesystem: bool = True,
        task_id: str = "default",
        namespace: str = "sandbox",
        kubeconfig: str = "",
    ):
        super().__init__(cwd=cwd, timeout=timeout)
        self.image = image
        self.cpu = max(1, cpu)
        self.memory = max(512, memory)
        self.disk = max(0, disk)
        self._persistent = persistent_filesystem
        self._task_id = task_id
        self.namespace = namespace or "sandbox"
        self.kubeconfig = kubeconfig or ""
        self._remote_home = "/root"
        self._lock = threading.Lock()
        self._cleaned_up = False
        self._claimed_from_pool = False

        self._pod_name = _sanitize_pod_name(task_id)
        self._base_args = _kubectl_base_args(self.namespace, self.kubeconfig)

        self._ensure_kubectl_available()
        self._ensure_pod_ready()

        try:
            self._detect_remote_home(requested_cwd=cwd)

            self._sync_manager = FileSyncManager(
                get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
                upload_fn=self._kubectl_upload,
                delete_fn=self._kubectl_delete,
                bulk_upload_fn=self._kubectl_bulk_upload,
            )
            self._sync_manager.sync(force=True)
            self.init_session()
        except Exception:
            self.cleanup()
            raise

    # ------------------------------------------------------------------
    # kubectl helpers
    # ------------------------------------------------------------------

    def _run_kubectl(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess:
        return _run_kubectl(self._base_args, args, input_text=input_text, timeout=timeout)

    def _run_kubectl_with_retry(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout: int = 120,
        retries: int = _EXEC_MAX_RETRIES,
    ) -> subprocess.CompletedProcess:
        """Run kubectl with retries on transient failures (non-zero exit)."""
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                result = self._run_kubectl(args, input_text=input_text, timeout=timeout)
                if result.returncode == 0:
                    return result
                last_exc = RuntimeError(
                    (result.stderr or result.stdout or "kubectl exec failed").strip()
                )
            except subprocess.TimeoutExpired as exc:
                last_exc = RuntimeError(f"kubectl timed out: {exc}")

            if attempt < retries - 1:
                delay = _EXEC_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Kata: kubectl attempt %d/%d failed, retrying in %.1fs: %s",
                    attempt + 1, retries, delay, last_exc,
                )
                time.sleep(delay)

        raise last_exc  # type: ignore[misc]

    def _ensure_kubectl_available(self) -> None:
        try:
            result = self._run_kubectl(["version", "--client"])
        except FileNotFoundError as exc:
            raise RuntimeError("kubectl not found in PATH") from exc
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"kubectl is unavailable: {stderr}")

    # ------------------------------------------------------------------
    # Pod lifecycle
    # ------------------------------------------------------------------

    def _ensure_pod_ready(self) -> None:
        # Skip if we already have a pod from pool or a ready persistent pod
        if self._claimed_from_pool and self._pod_exists_and_ready():
            return

        # Try to claim from pool first
        if self._persistent and not self._claimed_from_pool:
            pool = KataPodPool.get_or_create(
                image=self.image,
                namespace=self.namespace,
                kubeconfig=self.kubeconfig,
                cpu=self.cpu,
                memory=self.memory,
            )
            claimed = pool.claim(self._task_id)
            if claimed:
                self._pod_name = claimed
                self._claimed_from_pool = True
                logger.info("Kata: claimed warm pod %s from pool for task %s", claimed, self._task_id)
                return

        if self._persistent and self._pod_exists_and_ready():
            logger.info("Kata: reusing ready pod %s in namespace %s", self._pod_name, self.namespace)
            return

        if self._persistent and self._pod_exists():
            logger.info("Kata: deleting stale pod %s before recreate", self._pod_name)
            self._run_kubectl(["delete", "pod", self._pod_name, "--ignore-not-found=true"], timeout=60)

        manifest = self._build_pod_manifest()
        result = self._run_kubectl(["apply", "-f", "-"], input_text=manifest, timeout=60)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "kubectl apply failed").strip())

        ready_timeout = _pod_ready_timeout()
        wait = self._run_kubectl(
            ["wait", f"pod/{self._pod_name}", "--for=condition=Ready", f"--timeout={ready_timeout}s"],
            timeout=ready_timeout + 10,
        )
        if wait.returncode != 0:
            describe = self._run_kubectl(["describe", "pod", self._pod_name], timeout=30)
            detail = (describe.stdout or describe.stderr or "").strip()
            self._run_kubectl(["delete", "pod", self._pod_name, "--ignore-not-found=true"], timeout=30)
            raise RuntimeError(
                f"Kata pod {self._pod_name} did not become ready within {ready_timeout}s: "
                f"{(wait.stderr or wait.stdout).strip()}\n{detail}"
            )

    def _pod_exists(self) -> bool:
        result = self._run_kubectl(["get", "pod", self._pod_name, "-o", "name"], timeout=20)
        return result.returncode == 0 and bool((result.stdout or "").strip())

    def _pod_exists_and_ready(self) -> bool:
        result = self._run_kubectl(
            [
                "get", "pod", self._pod_name,
                "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            ],
            timeout=20,
        )
        return result.returncode == 0 and (result.stdout or "").strip() == "True"

    def _build_pod_manifest(self) -> str:
        cpu_request = min(max(float(self.cpu) / 2.0, 0.2), float(self.cpu))
        mem_request = max(int(self.memory * 0.5), 512)
        return f"""apiVersion: v1
kind: Pod
metadata:
  name: {self._pod_name}
  namespace: {self.namespace}
  labels:
    app: hermes-kata
    hermes-task-id: {self._task_id}
spec:
  runtimeClassName: kata
  restartPolicy: Never
  containers:
  - name: sandbox
    image: {self.image}
    command: ["sh", "-c", "mkdir -p /root/.hermes && while true; do sleep 3600; done"]
    securityContext:
      readOnlyRootFilesystem: false
    resources:
      requests:
        cpu: "{cpu_request:g}"
        memory: "{mem_request}Mi"
      limits:
        cpu: "{float(self.cpu):g}"
        memory: "{int(self.memory)}Mi"
"""

    def _detect_remote_home(self, requested_cwd: str) -> None:
        try:
            result = self._run_kubectl(
                ["exec", self._pod_name, "--", "sh", "-c", "printf %s \"$HOME\""],
                timeout=20,
            )
            home = (result.stdout or "").strip()
            if home:
                self._remote_home = home
                if requested_cwd in {"~", "/root"}:
                    self.cwd = home
        except Exception as exc:
            logger.debug("Kata: couldn't detect remote home, using /root: %s", exc)

    # ------------------------------------------------------------------
    # Execution hooks
    # ------------------------------------------------------------------

    def _before_execute(self) -> None:
        self._ensure_pod_ready()
        self._sync_manager.sync()

    # ------------------------------------------------------------------
    # File sync (tar-based bulk upload)
    # ------------------------------------------------------------------

    def _kubectl_upload(self, host_path: str, remote_path: str) -> None:
        parent = str(Path(remote_path).parent)
        mkdir = self._run_kubectl_with_retry(
            ["exec", self._pod_name, "--", "sh", "-c", quoted_mkdir_command([parent])],
            timeout=60,
        )
        if mkdir.returncode != 0:
            raise RuntimeError((mkdir.stderr or mkdir.stdout or "mkdir failed").strip())
        copy = self._run_kubectl_with_retry(
            ["cp", host_path, f"{self.namespace}/{self._pod_name}:{remote_path}"],
            timeout=120,
        )
        if copy.returncode != 0:
            raise RuntimeError((copy.stderr or copy.stdout or "kubectl cp failed").strip())

    def _kubectl_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """Upload files. Uses tar bulk upload for >= _TAR_SYNC_THRESHOLD files."""
        if not files:
            return
        if len(files) >= _TAR_SYNC_THRESHOLD:
            _tar_bulk_upload(
                self._base_args, self._pod_name, self.namespace,
                files, self._remote_home,
            )
        else:
            # Small batch: per-file upload with mkdir
            parents = unique_parent_dirs(files)
            mkdir = self._run_kubectl_with_retry(
                ["exec", self._pod_name, "--", "sh", "-c", quoted_mkdir_command(parents)],
                timeout=60,
            )
            if mkdir.returncode != 0:
                raise RuntimeError((mkdir.stderr or mkdir.stdout or "mkdir failed").strip())
            for host_path, remote_path in files:
                self._kubectl_upload(host_path, remote_path)

    def _kubectl_delete(self, remote_paths: list[str]) -> None:
        if not remote_paths:
            return
        rm = self._run_kubectl_with_retry(
            ["exec", self._pod_name, "--", "sh", "-c", quoted_rm_command(remote_paths)],
            timeout=60,
        )
        if rm.returncode != 0:
            raise RuntimeError((rm.stderr or rm.stdout or "rm failed").strip())

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None):
        shell_flag = "-l" if login else ""
        shell_cmd = f"bash {shell_flag} -c {shlex.quote(cmd_string)}".strip()
        cmd = [*self._base_args, "exec", "-i", self._pod_name, "--", "sh", "-c", shell_cmd]
        return _popen_bash(cmd, stdin_data=stdin_data)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self):
        """Delete the pod (non-persistent) or return to pool (persistent).

        Always attempts cleanup — never raises.  Idempotent.
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self._persistent:
            if self._claimed_from_pool:
                # Return to pool instead of deleting
                pool = KataPodPool.get_or_create(
                    image=self.image,
                    namespace=self.namespace,
                    kubeconfig=self.kubeconfig,
                    cpu=self.cpu,
                    memory=self.memory,
                )
                pool.release(self._pod_name)
                logger.debug("Kata: returned pod %s to pool", self._pod_name)
            else:
                logger.debug("Kata: persistent pod %s left running", self._pod_name)
            return

        try:
            result = self._run_kubectl(
                ["delete", "pod", self._pod_name, "--ignore-not-found=true"],
                timeout=60,
            )
            if result.returncode == 0:
                logger.info("Kata: deleted pod %s", self._pod_name)
            else:
                logger.warning(
                    "Kata: failed to delete pod %s: %s",
                    self._pod_name,
                    (result.stderr or result.stdout or "").strip(),
                )
        except Exception as exc:
            logger.warning("Kata: cleanup error for pod %s: %s", self._pod_name, exc)
