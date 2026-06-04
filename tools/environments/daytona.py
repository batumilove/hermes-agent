"""Daytona cloud execution environment.

Uses the Daytona Python SDK to run commands in cloud sandboxes.
Supports persistent sandboxes: when enabled, sandboxes are stopped on cleanup
and resumed on next creation, preserving the filesystem across sessions.
"""

import logging
import math
import os
import shlex
import threading
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
)
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)


class DaytonaEnvironment(BaseEnvironment):
    """Daytona cloud sandbox execution backend.

    Spawn-per-call via _ThreadedProcessHandle wrapping blocking SDK calls.
    cancel_fn wired to sandbox.stop() for interrupt support.
    Shell timeout wrapper preserved (SDK timeout unreliable).
    """

    _stdin_mode = "heredoc"

    def __init__(
        self,
        image: str,
        cwd: str = "/home/daytona",
        timeout: int = 60,
        cpu: int = 1,
        memory: int = 5120,
        disk: int = 10240,
        persistent_filesystem: bool = True,
        task_id: str = "default",
    ):
        requested_cwd = cwd
        super().__init__(cwd=cwd, timeout=timeout)

        try:
            from tools.lazy_deps import ensure as _lazy_ensure
            _lazy_ensure("terminal.daytona", prompt=False)
        except ImportError:
            pass
        except Exception as e:
            raise ImportError(str(e))
        from daytona_sdk import (
            Daytona,
            DaytonaConfig,
            CreateSandboxFromImageParams,
            DaytonaError,
            ListSandboxesQuery,
            Resources,
            SandboxState,
        )

        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._SandboxState = SandboxState
        self._ListSandboxesQuery = ListSandboxesQuery
        self._daytona = Daytona(self._build_daytona_config(DaytonaConfig))
        self._sandbox = None
        self._lock = threading.Lock()

        memory_gib = max(1, math.ceil(memory / 1024))
        disk_gib = max(1, math.ceil(disk / 1024))
        if disk_gib > 10:
            logger.warning(
                "Daytona: requested disk (%dGB) exceeds platform limit (10GB). "
                "Capping to 10GB.", disk_gib,
            )
            disk_gib = 10
        resources = Resources(cpu=cpu, memory=memory_gib, disk=disk_gib)

        labels = {"hermes_task_id": task_id}
        sandbox_name = f"hermes-{task_id}"

        if self._persistent:
            try:
                self._sandbox = self._daytona.get(sandbox_name)
                self._sandbox.start()
                logger.info("Daytona: resumed sandbox %s for task %s",
                            self._sandbox.id, task_id)
            except DaytonaError:
                self._sandbox = None
            except Exception as e:
                logger.warning("Daytona: failed to resume sandbox for task %s: %s",
                               task_id, e)
                self._sandbox = None

            if self._sandbox is None:
                try:
                    # Daytona SDK >=0.108.0 uses cursor-based pagination and
                    # list() returns an iterator. Offset-based pagination
                    # (page=1) is removed on June 10, 2026.
                    results = self._daytona.list(
                        self._ListSandboxesQuery(labels=labels, limit=1)
                    )
                    legacy = next(iter(results), None)
                    if legacy is not None:
                        self._sandbox = legacy
                        self._sandbox.start()
                        logger.info("Daytona: resumed legacy sandbox %s for task %s",
                                    self._sandbox.id, task_id)
                except Exception as e:
                    logger.debug("Daytona: no legacy sandbox found for task %s: %s",
                                 task_id, e)
                    self._sandbox = None

        if self._sandbox is None:
            self._sandbox = self._daytona.create(
                CreateSandboxFromImageParams(
                    image=image,
                    name=sandbox_name,
                    labels=labels,
                    auto_stop_interval=0,
                    env_vars={"LANG": "C", "LC_ALL": "C"},
                    resources=resources,
                )
            )
            logger.info("Daytona: created sandbox %s for task %s",
                        self._sandbox.id, task_id)

        self._normalize_toolbox_proxy_url()

        # Detect remote home dir
        self._remote_home = "/root"
        try:
            home = self._sandbox.process.exec("echo $HOME").result.strip()
            if home:
                self._remote_home = home
                if requested_cwd in {"~", "/home/daytona"}:
                    self.cwd = home
        except Exception:
            pass
        logger.info("Daytona: resolved home to %s, cwd to %s", self._remote_home, self.cwd)

        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
            upload_fn=self._daytona_upload,
            delete_fn=self._daytona_delete,
            bulk_upload_fn=self._daytona_bulk_upload,
            bulk_download_fn=self._daytona_bulk_download,
        )
        self._sync_manager.sync(force=True)
        self.init_session()

    @staticmethod
    def _normalize_api_url(api_url: str | None) -> str | None:
        """Return a Daytona SDK API base URL.

        Self-hosted Daytona commonly exposes the dashboard at ``:3000`` and
        the API under ``:3000/api``. The SDK expects the latter; when given
        the dashboard root it receives HTML and fails deserializing responses.
        """
        if not api_url:
            return api_url
        normalized = api_url.rstrip("/")
        return normalized if normalized.endswith("/api") else normalized + "/api"

    @classmethod
    def _build_daytona_config(cls, DaytonaConfig):
        api_url = cls._normalize_api_url(os.getenv("DAYTONA_API_URL"))
        api_key = os.getenv("DAYTONA_API_KEY")
        if api_url or api_key:
            return DaytonaConfig(api_key=api_key, api_url=api_url)
        return DaytonaConfig()

    def _normalize_toolbox_proxy_url(self) -> None:
        """Rewrite self-hosted Docker Compose toolbox URLs for remote clients.

        Daytona's Docker Compose defaults can return ``proxy.localhost:4000``
        in sandbox DTOs. That works only from the Daytona VM itself; Hermes often
        runs on a different host and reaches Daytona via ``DAYTONA_API_URL``.
        When the toolbox proxy points at localhost, rewrite it to the API host
        while preserving the proxy port/path.
        """
        toolbox_url = getattr(self._sandbox, "toolbox_proxy_url", None)
        api_url = os.getenv("DAYTONA_API_URL")
        if not isinstance(toolbox_url, str) or not toolbox_url or not api_url:
            return

        parsed_toolbox = urlparse(toolbox_url)
        if parsed_toolbox.hostname not in {"localhost", "127.0.0.1", "proxy.localhost"}:
            return

        parsed_api = urlparse(api_url)
        if not parsed_api.hostname:
            return

        host = parsed_api.hostname
        if parsed_toolbox.port:
            host = f"{host}:{parsed_toolbox.port}"
        # Preserve the operator-configured API scheme. Docker Compose may report
        # an http://proxy.localhost URL, but remote clients should not downgrade
        # a https://DAYTONA_API_URL deployment to plaintext while only swapping
        # hosts.
        scheme = parsed_api.scheme or parsed_toolbox.scheme
        rewritten = urlunparse(parsed_toolbox._replace(scheme=scheme, netloc=host))
        self._sandbox.toolbox_proxy_url = rewritten
        toolbox_api = getattr(self._sandbox, "_toolbox_api", None)
        if toolbox_api is not None and hasattr(toolbox_api, "_toolbox_base_url"):
            toolbox_api._toolbox_base_url = rewritten
        logger.info("Daytona: rewrote toolbox proxy URL %s -> %s", toolbox_url, rewritten)

    def _daytona_upload(self, host_path: str, remote_path: str) -> None:
        """Upload a single file via Daytona SDK."""
        parent = str(Path(remote_path).parent)
        self._sandbox.process.exec(quoted_mkdir_command([parent]))
        self._sandbox.fs.upload_file(host_path, remote_path)

    def _daytona_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """Upload many files in a single HTTP call via Daytona SDK.

        Uses ``sandbox.fs.upload_files()`` which batches all files into one
        multipart POST, avoiding per-file TLS/HTTP overhead (~580 files
        goes from ~5 min to <2 s).
        """
        from daytona_sdk.common.filesystem import FileUpload

        if not files:
            return

        parents = unique_parent_dirs(files)
        if parents:
            self._sandbox.process.exec(quoted_mkdir_command(parents))

        for start in range(0, len(files), 100):
            chunk = files[start:start + 100]
            uploads = [
                FileUpload(source=host_path, destination=remote_path)
                for host_path, remote_path in chunk
            ]
            self._sandbox.fs.upload_files(uploads)

    def _daytona_bulk_download(self, dest: Path) -> None:
        """Download remote .hermes/ as a tar archive."""
        rel_base = f"{self._remote_home}/.hermes".lstrip("/")
        # PID-suffixed remote temp path avoids collisions if sync_back fires
        # concurrently for the same sandbox (e.g. retry after partial failure).
        remote_tar = f"/tmp/.hermes_sync.{os.getpid()}.tar"
        self._sandbox.process.exec(
            f"tar cf {shlex.quote(remote_tar)} -C / {shlex.quote(rel_base)}"
        )
        self._sandbox.fs.download_file(remote_tar, str(dest))
        # Clean up remote temp file
        try:
            self._sandbox.process.exec(f"rm -f {shlex.quote(remote_tar)}")
        except Exception:
            pass  # best-effort cleanup

    def _daytona_delete(self, remote_paths: list[str]) -> None:
        """Batch-delete remote files via SDK exec."""
        self._sandbox.process.exec(quoted_rm_command(remote_paths))

    # ------------------------------------------------------------------
    # Sandbox lifecycle
    # ------------------------------------------------------------------

    def _ensure_sandbox_ready(self) -> None:
        """Restart sandbox if it was stopped (e.g., by a previous interrupt)."""
        self._sandbox.refresh_data()
        if self._sandbox.state in {self._SandboxState.STOPPED, self._SandboxState.ARCHIVED}:
            self._sandbox.start()
            logger.info("Daytona: restarted sandbox %s", self._sandbox.id)

    def _before_execute(self) -> None:
        """Ensure sandbox is ready, then sync files via FileSyncManager."""
        with self._lock:
            self._ensure_sandbox_ready()
        self._sync_manager.sync()

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None):
        """Return a _ThreadedProcessHandle wrapping a blocking Daytona SDK call."""
        sandbox = self._sandbox
        lock = self._lock

        def cancel():
            with lock:
                try:
                    sandbox.stop()
                except Exception:
                    pass

        if login:
            shell_cmd = f"bash -l -c {shlex.quote(cmd_string)}"
        else:
            shell_cmd = f"bash -c {shlex.quote(cmd_string)}"

        def exec_fn() -> tuple[str, int]:
            response = sandbox.process.exec(shell_cmd, timeout=timeout)
            return (response.result or "", response.exit_code)

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        with self._lock:
            if self._sandbox is None:
                return

            # Sync remote changes back to host before teardown. Running
            # inside the lock (and after the _sandbox is None guard) avoids
            # firing sync_back on an already-cleaned-up env, which would
            # trigger a 3-attempt retry storm against a nil sandbox.
            if self._sync_manager:
                logger.info("Daytona: syncing files from sandbox...")
                try:
                    self._sync_manager.sync_back()
                except Exception as e:
                    logger.warning("Daytona: sync_back failed: %s", e)

            try:
                if self._persistent:
                    self._sandbox.stop()
                    logger.info("Daytona: stopped sandbox %s (filesystem preserved)",
                                self._sandbox.id)
                else:
                    self._daytona.delete(self._sandbox)
                    logger.info("Daytona: deleted sandbox %s", self._sandbox.id)
            except Exception as e:
                logger.warning("Daytona: cleanup failed: %s", e)
            self._sandbox = None
