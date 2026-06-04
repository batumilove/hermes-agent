import os
import subprocess
import tarfile
import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from tools.environments.kata import (
    KataEnvironment,
    KataPodPool,
    _sanitize_pod_name,
    _tar_bulk_upload,
    _is_remote_sync_path_allowed,
    _remote_hermes_base,
    cleanup_orphaned_pods,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_env(monkeypatch, tmp_path, *, ready=True, persistent=True):
    # Disable pool so tests don't trigger reconciler
    monkeypatch.setenv("KATA_POOL_SIZE", "0")
    monkeypatch.setattr("tools.environments.base.is_interrupted", lambda: False)
    monkeypatch.setattr("tools.credential_files.get_credential_file_mounts", lambda: [])
    monkeypatch.setattr("tools.credential_files.get_skills_directory_mount", lambda **kw: None)
    monkeypatch.setattr("tools.credential_files.iter_skills_files", lambda **kw: [])

    calls = []

    def fake_run(cmd, input=None, text=None, capture_output=None, timeout=None, **kwargs):
        calls.append(cmd)
        joined = " ".join(cmd)
        if "version --client" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="kubectl ok", stderr="")
        if "get pod" in joined and "jsonpath=" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="True" if ready else "False", stderr="")
        if "get pod" in joined:
            return subprocess.CompletedProcess(cmd, 0 if ready else 1, stdout="pod/hermes-kata-testtask", stderr="")
        if "exec" in joined and "printf %s \"$HOME\"" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="/root", stderr="")
        if "exec" in joined and quoted_contains(joined, "export -p"):
            return subprocess.CompletedProcess(cmd, 0, stdout="\n__HERMES_CWD_123__/root__HERMES_CWD_123__\n", stderr="")
        if "exec" in joined and quoted_contains(joined, "echo hello"):
            return subprocess.CompletedProcess(cmd, 0, stdout="hello\n__HERMES_CWD_123__/root__HERMES_CWD_123__\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    env = KataEnvironment(
        image="python:3.12-slim",
        task_id="testtask",
        namespace="sandbox",
        persistent_filesystem=persistent,
    )
    return env, calls


def quoted_contains(text: str, needle: str) -> bool:
    return needle in text.replace("'", "")


# ---------------------------------------------------------------------------
# Pod name sanitisation
# ---------------------------------------------------------------------------


class TestSanitizePodName:
    def test_basic(self):
        assert _sanitize_pod_name("my-task") == "hermes-kata-my-task"

    def test_underscores_replaced(self):
        assert _sanitize_pod_name("my_task") == "hermes-kata-my-task"

    def test_uppercase_lowered(self):
        assert _sanitize_pod_name("MY_Task") == "hermes-kata-my-task"

    def test_special_chars_stripped(self):
        name = _sanitize_pod_name("task@#$%123")
        assert all(c.isalnum() or c == "-" for c in name)

    def test_truncation_to_63(self):
        name = _sanitize_pod_name("a" * 200)
        assert len(name) <= 63
        assert not name.endswith("-")

    def test_no_trailing_hyphens(self):
        name = _sanitize_pod_name("task---")
        assert not name.endswith("-")

    def test_empty_task_id_fallback(self):
        name = _sanitize_pod_name("")
        assert name.startswith("hermes-kata-")
        assert len(name) > len("hermes-kata-")  # has random suffix

    def test_consecutive_hyphens_collapsed(self):
        name = _sanitize_pod_name("a---b")
        assert "---" not in name


# ---------------------------------------------------------------------------
# Pod lifecycle
# ---------------------------------------------------------------------------


def test_reuses_ready_pod(monkeypatch, tmp_path):
    env, calls = _make_env(monkeypatch, tmp_path, ready=True)
    assert env.namespace == "sandbox"
    assert any("version" in " ".join(c) for c in calls)
    assert not any("apply -f -" in " ".join(c) for c in calls)


def test_execute_runs_via_kubectl_exec(monkeypatch, tmp_path):
    env, calls = _make_env(monkeypatch, tmp_path, ready=True)
    popen_calls = []

    from tools.environments.base import _ThreadedProcessHandle

    monkeypatch.setattr(
        "tools.environments.kata._popen_bash",
        lambda cmd, stdin_data=None: popen_calls.append(cmd) or _ThreadedProcessHandle(lambda: ("hello\n", 0)),
    )
    result = env.execute("echo hello")
    assert result["returncode"] == 0
    assert "hello" in result["output"]
    assert any("exec -i hermes-kata-testtask" in " ".join(c) for c in popen_calls)


def test_nonpersistent_cleanup_deletes_pod(monkeypatch, tmp_path):
    monkeypatch.setenv("KATA_POOL_SIZE", "0")
    deletions = []

    def fake_run(cmd, input=None, text=None, capture_output=None, timeout=None, **kwargs):
        joined = " ".join(cmd)
        if "delete pod hermes-kata-testtask" in joined:
            deletions.append(joined)
        if "version --client" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="kubectl ok", stderr="")
        if "get pod" in joined and "jsonpath=" in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if "get pod" in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if "apply -f -" in joined or "wait pod/" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "printf %s \"$HOME\"" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="/root", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="\n__HERMES_CWD_123__/root__HERMES_CWD_123__\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("tools.environments.base.is_interrupted", lambda: False)
    monkeypatch.setattr("tools.credential_files.get_credential_file_mounts", lambda: [])
    monkeypatch.setattr("tools.credential_files.get_skills_directory_mount", lambda **kw: None)
    monkeypatch.setattr("tools.credential_files.iter_skills_files", lambda **kw: [])
    env = KataEnvironment(image="python:3.12-slim", task_id="testtask", namespace="sandbox", persistent_filesystem=False)
    env.cleanup()
    assert deletions


def test_cleanup_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("KATA_POOL_SIZE", "0")
    deletions = []

    def fake_run(cmd, input=None, text=None, capture_output=None, timeout=None, **kwargs):
        joined = " ".join(cmd)
        if "delete pod hermes-kata-testtask" in joined:
            deletions.append(joined)
        if "version --client" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="kubectl ok", stderr="")
        if "get pod" in joined and "jsonpath=" in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if "get pod" in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if "apply -f -" in joined or "wait pod/" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "printf %s \"$HOME\"" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="/root", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="\n__HERMES_CWD_123__/root__HERMES_CWD_123__\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("tools.environments.base.is_interrupted", lambda: False)
    monkeypatch.setattr("tools.credential_files.get_credential_file_mounts", lambda: [])
    monkeypatch.setattr("tools.credential_files.get_skills_directory_mount", lambda **kw: None)
    monkeypatch.setattr("tools.credential_files.iter_skills_files", lambda **kw: [])
    env = KataEnvironment(image="python:3.12-slim", task_id="testtask", namespace="sandbox", persistent_filesystem=False)
    env.cleanup()
    env.cleanup()
    assert len(deletions) == 1


def test_cleanup_does_not_raise_on_kubectl_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("KATA_POOL_SIZE", "0")
    def fake_run(cmd, input=None, text=None, capture_output=None, timeout=None, **kwargs):
        joined = " ".join(cmd)
        if "version --client" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="kubectl ok", stderr="")
        if "get pod" in joined and "jsonpath=" in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if "get pod" in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if "apply -f -" in joined or "wait pod/" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "printf %s \"$HOME\"" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="/root", stderr="")
        if "delete pod" in joined:
            raise subprocess.TimeoutExpired(cmd, 60)
        return subprocess.CompletedProcess(cmd, 0, stdout="\n__HERMES_CWD_123__/root__HERMES_CWD_123__\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("tools.environments.base.is_interrupted", lambda: False)
    monkeypatch.setattr("tools.credential_files.get_credential_file_mounts", lambda: [])
    monkeypatch.setattr("tools.credential_files.get_skills_directory_mount", lambda **kw: None)
    monkeypatch.setattr("tools.credential_files.iter_skills_files", lambda **kw: [])
    env = KataEnvironment(image="python:3.12-slim", task_id="testtask", namespace="sandbox", persistent_filesystem=False)
    env.cleanup()


def test_persistent_cleanup_leaves_pod(monkeypatch, tmp_path):
    env, calls = _make_env(monkeypatch, tmp_path, ready=True, persistent=True)
    pre_delete_count = sum(1 for c in calls if "delete" in " ".join(c))
    env.cleanup()
    post_delete_count = sum(1 for c in calls if "delete" in " ".join(c))
    assert post_delete_count == pre_delete_count


def test_resource_minimums_enforced(monkeypatch, tmp_path):
    monkeypatch.setenv("KATA_POOL_SIZE", "0")
    monkeypatch.setattr("tools.environments.base.is_interrupted", lambda: False)
    monkeypatch.setattr("tools.credential_files.get_credential_file_mounts", lambda: [])
    monkeypatch.setattr("tools.credential_files.get_skills_directory_mount", lambda **kw: None)
    monkeypatch.setattr("tools.credential_files.iter_skills_files", lambda **kw: [])

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "version --client" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        if "get pod" in joined and "jsonpath=" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="True", stderr="")
        if "get pod" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="pod/hermes-kata-t", stderr="")
        if "printf" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="/root", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    env = KataEnvironment(image="python:3.12-slim", task_id="t", cpu=0, memory=100)
    assert env.cpu == 1
    assert env.memory == 512


# ---------------------------------------------------------------------------
# Tar bulk upload
# ---------------------------------------------------------------------------


class TestTarBulkUpload:
    def test_creates_tar_and_uploads(self, tmp_path):
        """Verify tar bulk upload creates archive and calls kubectl correctly."""
        f1 = tmp_path / "a.txt"
        f1.write_text("hello")
        f2 = tmp_path / "b.txt"
        f2.write_text("world")

        files = [(str(f1), "/root/.hermes/a.txt"), (str(f2), "/root/.hermes/b.txt")]

        calls = []

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            calls.append(cmd)
            joined = " ".join(cmd)
            if "mkdir" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "cp " in joined and ".tar.gz" in joined:
                src = extra_args[extra_args.index("cp") + 1]
                assert os.path.exists(src), f"Tar file {src} should exist"
                with tarfile.open(src, "r:gz") as tar:
                    names = tar.getnames()
                    assert "root/.hermes/a.txt" in names
                    assert "root/.hermes/b.txt" in names
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "tar xzf" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            _tar_bulk_upload(
                ["kubectl", "-n", "sandbox"],
                "test-pod",
                "sandbox",
                files,
                "/root",
            )

        assert len(calls) >= 3

    def test_fallback_on_tar_failure(self, tmp_path):
        """If tar upload fails, falls back to per-file upload."""
        f1 = tmp_path / "a.txt"
        f1.write_text("hello")

        files = [(str(f1), "/root/.hermes/a.txt")]

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            joined = " ".join(cmd)
            if "mkdir" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "cp " in joined and ".tar.gz" in joined:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="tar cp failed")
            if "cp " in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            _tar_bulk_upload(
                ["kubectl", "-n", "sandbox"],
                "test-pod",
                "sandbox",
                files,
                "/root",
            )

    def test_empty_files_noop(self):
        """No files → no kubectl calls."""
        calls = []

        def fake_run(*a, **kw):
            calls.append(a)
            return subprocess.CompletedProcess([], 0)

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            _tar_bulk_upload(["kubectl", "-n", "sandbox"], "pod", "sandbox", [], "/root")
        assert len(calls) == 0

    def test_rejects_remote_paths_outside_remote_hermes_base(self, tmp_path):
        """Tar sync must not archive or copy files outside remote .hermes."""
        good = tmp_path / "good.txt"
        good.write_text("safe")
        bad = tmp_path / "bad.txt"
        bad.write_text("unsafe")
        files = [(str(good), "/root/.hermes/good.txt"), (str(bad), "/root/.ssh/authorized_keys")]

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            joined = " ".join(cmd)
            assert "/root/.ssh" not in joined
            if "cp " in joined and ".tar.gz" in joined:
                src = extra_args[extra_args.index("cp") + 1]
                with tarfile.open(src, "r:gz") as tar:
                    assert tar.getnames() == ["root/.hermes/good.txt"]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            _tar_bulk_upload(["kubectl", "-n", "sandbox"], "pod", "sandbox", files, "/root")

    def test_remote_path_safety_helpers_normalize_base(self):
        assert _remote_hermes_base("/root/") == "/root/.hermes"
        assert _is_remote_sync_path_allowed("/root/.hermes/config.yaml", "/root/.hermes")
        assert not _is_remote_sync_path_allowed("/root/.hermes/../.ssh/id_rsa", "/root/.hermes")
        assert not _is_remote_sync_path_allowed("relative/.hermes/config.yaml", "/root/.hermes")


# ---------------------------------------------------------------------------
# KataPodPool
# ---------------------------------------------------------------------------


class TestKataPodPool:
    @pytest.fixture(autouse=True)
    def _clear_pool(self):
        """Clean up pool singletons between tests."""
        KataPodPool._clear_instances()
        yield
        KataPodPool._clear_instances()

    def test_claim_returns_none_when_empty(self):
        pool = KataPodPool(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="",
            pool_size=0,
        )
        assert pool.claim("task-1") is None

    def test_claim_returns_pod_when_available(self):
        pool = KataPodPool(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="",
            pool_size=0,
        )
        # Manually add a pod
        from tools.environments.kata import _PooledPod
        pod = _PooledPod("test-pod-1", "pool")
        pool._pods.append(pod)

        result = pool.claim("task-1")
        assert result == "test-pod-1"
        assert pod.in_use is True
        assert pod.task_id == "task-1"

    def test_claim_returns_none_when_all_in_use(self):
        pool = KataPodPool(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="",
            pool_size=0,
        )
        from tools.environments.kata import _PooledPod
        pod = _PooledPod("test-pod-1", "task-0")
        pod.in_use = True
        pool._pods.append(pod)

        assert pool.claim("task-1") is None

    def test_release_makes_pod_available(self):
        pool = KataPodPool(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="",
            pool_size=0,
        )
        from tools.environments.kata import _PooledPod
        pod = _PooledPod("test-pod-1", "pool")
        pool._pods.append(pod)

        pool.claim("task-1")
        assert pod.in_use is True

        pool.release("test-pod-1")
        assert pod.in_use is False

        # Can claim again
        assert pool.claim("task-2") == "test-pod-1"

    def test_shutdown_cleans_up(self):
        pool = KataPodPool(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="",
            pool_size=0,
        )
        from tools.environments.kata import _PooledPod
        pool._pods.append(_PooledPod("pod-1", "pool"))
        pool._pods.append(_PooledPod("pod-2", "pool"))

        # Mock kubectl delete
        calls = []
        with patch("tools.environments.kata._run_kubectl", side_effect=lambda *a, **kw: calls.append(a) or subprocess.CompletedProcess([], 0)):
            pool.shutdown()

        assert len(pool._pods) == 0

    def test_get_or_create_singleton(self):
        p1 = KataPodPool.get_or_create("img", "ns", "kube")
        p2 = KataPodPool.get_or_create("img", "ns", "kube")
        assert p1 is p2

    def test_get_or_create_different_keys(self):
        p1 = KataPodPool.get_or_create("img1", "ns", "kube")
        p2 = KataPodPool.get_or_create("img2", "ns", "kube")
        assert p1 is not p2

    def test_remove_deletes_from_k8s(self):
        pool = KataPodPool(
            image="python:3.12-slim",
            namespace="sandbox",
            kubeconfig="",
            pool_size=0,
        )
        from tools.environments.kata import _PooledPod
        pool._pods.append(_PooledPod("pod-1", "pool"))

        calls = []
        with patch("tools.environments.kata._run_kubectl", side_effect=lambda *a, **kw: calls.append(a) or subprocess.CompletedProcess([], 0)):
            pool.remove("pod-1")

        assert len(pool._pods) == 0
        assert any("delete" in " ".join(str(a) for a in c) for c in calls)

    def test_eager_bootstrap_creates_first_pod(self):
        """Pool with size>0 eagerly creates one pod synchronously."""
        call_log = []

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            joined = " ".join(cmd)
            call_log.append(joined)

            if "version --client" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
            # GC list returns empty
            if "get pods" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            # Pod apply
            if "apply -f -" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            # Pod wait
            if "wait pod/" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            # Exec (home detect, mkdir, tar upload, extract)
            if "exec" in joined:
                if "printf" in joined:
                    return subprocess.CompletedProcess(cmd, 0, stdout="/root", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            # kubectl cp
            if "cp " in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        # Patch iter_sync_files to return empty (skip sync)
        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            with patch("tools.environments.kata.iter_sync_files", return_value=iter([])):
                # Use pool_size=1 so reconciler doesn't create extras
                pool = KataPodPool(
                    image="python:3.12-slim",
                    namespace="sandbox",
                    kubeconfig="",
                    pool_size=1,
                )

        assert len(pool._pods) >= 1  # at least eagerly bootstrapped
        idle = [p for p in pool._pods if not p.in_use]
        assert len(idle) >= 1  # at least one idle pod ready for claim
        pool.shutdown()


# ---------------------------------------------------------------------------
# Orphan garbage collection
# ---------------------------------------------------------------------------


class TestOrphanGC:
    def test_no_orphans_returns_zero(self):
        """No pods → returns 0, no deletions."""
        calls = []

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            calls.append(" ".join(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            result = cleanup_orphaned_pods("sandbox", "", max_age_seconds=3600)

        assert result == 0
        assert not any("delete" in c for c in calls)

    def test_deletes_old_pods(self):
        """Pods older than threshold get deleted."""
        import datetime
        old_time = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
        ).isoformat()

        list_output = f"old-pod-1\t{old_time}\n"

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            joined = " ".join(cmd)
            if "get pods" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout=list_output, stderr="")
            if "delete pod old-pod-1" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="deleted", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            result = cleanup_orphaned_pods("sandbox", "", max_age_seconds=3600)

        assert result == 1

    def test_skips_recent_pods(self):
        """Pods younger than threshold are left alone."""
        import datetime
        recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

        list_output = f"fresh-pod\t{recent_time}\n"

        def fake_run(base_args, extra_args, *, input_text=None, timeout=120):
            cmd = [*base_args, *extra_args]
            joined = " ".join(cmd)
            if "get pods" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout=list_output, stderr="")
            if "delete" in joined:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("tools.environments.kata._run_kubectl", side_effect=fake_run):
            result = cleanup_orphaned_pods("sandbox", "", max_age_seconds=7200)

        assert result == 0
