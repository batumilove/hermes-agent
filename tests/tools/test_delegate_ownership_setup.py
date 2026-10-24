"""Delegation construction, submission, and credential ownership regressions."""
import json
import unittest
from concurrent.futures import Future
from unittest.mock import MagicMock, patch
from tests.tools.test_delegate import _make_mock_parent
from tools.delegate_tool import delegate_task, _build_child_agent, _run_single_child

class TestRetainedForkOwnership(unittest.TestCase):

    def test_child_close_gate_exists_before_parent_registration(self):
        """Parent close during batch construction must prevent later execution."""
        parent = _make_mock_parent(depth=0)
        parent._session_db = None

        class _CloseOnAppend(list):
            hook_was_present = False

            def append(self, child):
                request_close = getattr(child, "_delegate_request_close", None)
                self.hook_was_present = callable(request_close)
                if self.hook_was_present:
                    assert callable(request_close)
                    request_close()
                else:
                    child.close()
                super().append(child)

        active_children = _CloseOnAppend()
        parent._active_children = active_children
        child = MagicMock()
        child._credential_pool = None
        child._credential_pool = None
        child._subagent_id = None
        child._delegate_depth = 1
        child._parent_subagent_id = None
        child.run_conversation.return_value = {
            "final_response": "must not run",
            "completed": True,
            "api_calls": 1,
        }

        with patch("run_agent.AIAgent", return_value=child):
            built = _build_child_agent(
                task_index=0,
                goal="test pre-registration close",
                context=None,
                toolsets=None,
                model="test-model",
                max_iterations=5,
                parent_agent=parent,
                task_count=2,
            )

        self.assertTrue(active_children.hook_was_present)
        result = _run_single_child(
            task_index=0,
            goal="test pre-registration close",
            child=built,
            parent_agent=parent,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("closed before execution", result["error"])
        child.run_conversation.assert_not_called()

    def test_batch_build_failure_retires_already_registered_children(self):
        """A later construction failure cannot strand earlier batch children."""
        from tools.delegate_tool import _install_deferred_child_close_gate

        parent = _make_mock_parent(depth=0)
        child = MagicMock()
        child._credential_pool = None
        child._delegate_role = "leaf"
        child._credential_pool = None
        _install_deferred_child_close_gate(child)
        calls = 0

        def build(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("second child build failed")
            parent._active_children.append(child)
            return child

        with patch(
            "tools.delegate_tool._build_child_preserving_parent_tools",
            side_effect=build,
        ):
            result = json.loads(
                delegate_task(
                    tasks=[
                        {"goal": "first valid batch task"},
                        {"goal": "second valid batch task"},
                    ],
                    parent_agent=parent,
                )
            )

        self.assertIn("second child build failed", result["error"])
        child.close.assert_called_once()
        self.assertNotIn(child, parent._active_children)

    def test_batch_submit_failure_retires_every_built_child(self):
        """Executor rejection cannot strand the prebuilt batch."""
        from tools.delegate_tool import _install_deferred_child_close_gate

        parent = _make_mock_parent(depth=0)
        children = []

        def build(**kwargs):
            child = MagicMock()
            child._credential_pool = None
            child._delegate_role = kwargs.get("role", "leaf")
            child._credential_pool = None
            child.get_activity_summary.return_value = {"api_call_count": 2}
            _install_deferred_child_close_gate(child)
            parent._active_children.append(child)
            children.append(child)
            return child

        class _RejectingExecutor:
            instances = []

            def __init__(self, **_kwargs):
                self.calls = 0
                self.first_future = Future()
                assert self.first_future.set_running_or_notify_cancel()
                self.instances.append(self)

            def submit(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return self.first_future
                raise RuntimeError("batch submit failed")

            def shutdown(self, *, wait, cancel_futures):
                self.shutdown_args = (wait, cancel_futures)

        with (
            patch(
                "tools.delegate_tool._build_child_preserving_parent_tools",
                side_effect=build,
            ),
            patch("tools.daemon_pool.DaemonThreadPoolExecutor", _RejectingExecutor),
        ):
            result = json.loads(
                delegate_task(
                    tasks=[
                        {"goal": "first submit batch task"},
                        {"goal": "second submit batch task"},
                    ],
                    parent_agent=parent,
                )
            )

        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["status"], "interrupted")
        self.assertEqual(result["results"][0]["api_calls"], 2)
        self.assertTrue(result["results"][0]["api_calls_incomplete"])
        self.assertEqual(result["results"][1]["status"], "error")
        self.assertEqual(_RejectingExecutor.instances[0].shutdown_args, (False, True))
        self.assertTrue(all("batch submit failed" in r["error"] for r in result["results"]))
        children[0].interrupt.assert_called_once()
        children[1].interrupt.assert_not_called()
        for child in children:
            child.close.assert_called_once()
            self.assertNotIn(child, parent._active_children)

    def test_batch_submit_failure_preserves_completed_prior_result(self):
        """A completed earlier future must not be replaced by fabricated zeros."""
        from tools.delegate_tool import _install_deferred_child_close_gate

        parent = _make_mock_parent(depth=0)
        children = []

        def build(**kwargs):
            child = MagicMock()
            child._credential_pool = None
            child._delegate_role = kwargs.get("role", "leaf")
            child._credential_pool = None
            _install_deferred_child_close_gate(child)
            parent._active_children.append(child)
            children.append(child)
            return child

        completed = {
            "task_index": 0,
            "status": "completed",
            "summary": "real prior result",
            "api_calls": 3,
            "duration_seconds": 1.25,
            "_child_role": "leaf",
        }

        class _CompleteThenRejectExecutor:
            def __init__(self, **_kwargs):
                self.calls = 0

            def submit(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    future = Future()
                    future.set_result(completed)
                    return future
                raise RuntimeError("later submit failed")

            def shutdown(self, *, wait, cancel_futures):
                self.shutdown_args = (wait, cancel_futures)

        with (
            patch(
                "tools.delegate_tool._build_child_preserving_parent_tools",
                side_effect=build,
            ),
            patch(
                "tools.daemon_pool.DaemonThreadPoolExecutor",
                _CompleteThenRejectExecutor,
            ),
        ):
            result = json.loads(
                delegate_task(
                    tasks=[
                        {"goal": "completed prior batch task"},
                        {"goal": "later rejected batch task"},
                    ],
                    parent_agent=parent,
                )
            )

        self.assertEqual(result["results"][0]["summary"], "real prior result")
        self.assertEqual(result["results"][0]["api_calls"], 3)
        self.assertEqual(result["results"][0]["status"], "completed")
        self.assertEqual(result["results"][1]["status"], "error")
        self.assertIn("later submit failed", result["results"][1]["error"])
        for child in children:
            child.interrupt.assert_not_called()
        for child in children:
            child.close.assert_called_once()
            self.assertNotIn(child, parent._active_children)

    def test_run_single_child_binds_exact_leased_credential_not_global_cursor(self):
        from tools.delegate_tool import _run_single_child

        leased_entry = MagicMock(id="cred-b")
        wrong_current = MagicMock(id="cred-a")
        child = MagicMock()
        child._credential_pool = None
        child._credential_pool = MagicMock()
        child._credential_pool.acquire_lease.return_value = "cred-b"
        child._credential_pool.current.return_value = wrong_current
        child._credential_pool.entries.return_value = [wrong_current, leased_entry]
        child.run_conversation.return_value = {
            "final_response": "done",
            "completed": True,
            "interrupted": False,
            "api_calls": 1,
            "messages": [],
        }

        result = _run_single_child(
            task_index=0,
            goal="Bind exact lease",
            child=child,
            parent_agent=_make_mock_parent(),
        )

        self.assertEqual(result["status"], "completed")
        child._swap_credential.assert_called_once_with(leased_entry)
        child._credential_pool.release_lease.assert_called_once_with("cred-b")

    def test_run_single_child_fails_closed_if_leased_entry_disappears(self):
        from tools.delegate_tool import _run_single_child

        child = MagicMock()

        child._credential_pool = None
        child._credential_pool = MagicMock()
        child._credential_pool.acquire_lease.return_value = "cred-gone"
        child._credential_pool.entries.return_value = []

        result = _run_single_child(
            task_index=0,
            goal="Do not use an unleased credential",
            child=child,
            parent_agent=_make_mock_parent(),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("cred-gone", result["error"])
        child.run_conversation.assert_not_called()
        child._credential_pool.release_lease.assert_called_once_with("cred-gone")

    def test_lease_acquisition_failure_still_closes_child(self):
        from tools.delegate_tool import _run_single_child

        child = MagicMock()

        child._credential_pool = None
        child._credential_pool = MagicMock()
        child._credential_pool.acquire_lease.side_effect = RuntimeError("lease failed")

        result = _run_single_child(
            task_index=2,
            goal="Trigger lease failure",
            child=child,
            parent_agent=_make_mock_parent(),
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("lease failed", result["error"])
        child.close.assert_called_once()
        child.run_conversation.assert_not_called()
