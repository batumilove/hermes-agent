from __future__ import annotations

from agent.conversation_loop import _is_local_processing_exception


def _raising_function(filename: str):
    namespace: dict[str, object] = {}
    exec(compile("def boom():\n    raise RuntimeError('boom')\n", filename, "exec"), namespace)
    return namespace["boom"]


def _wrapper_function(filename: str):
    namespace: dict[str, object] = {}
    exec(compile("def call(inner):\n    return inner()\n", filename, "exec"), namespace)
    return namespace["call"]


def _caught_exception(function, *args) -> RuntimeError:
    try:
        function(*args)
    except RuntimeError as exc:
        return exc
    raise AssertionError("fixture did not raise")


def test_targeted_local_helper_exception_is_local_processing() -> None:
    local = _raising_function("/tmp/agent_runtime_helpers.py")

    assert _is_local_processing_exception(_caught_exception(local)) is True


def test_api_helper_in_traceback_is_not_local_processing() -> None:
    local = _raising_function("/tmp/agent_runtime_helpers.py")
    api = _wrapper_function("/tmp/chat_completion_helpers.py")

    assert _is_local_processing_exception(_caught_exception(api, local)) is False


def test_unrelated_local_exception_is_not_misclassified() -> None:
    unrelated = _raising_function("/tmp/unrelated_module.py")

    assert _is_local_processing_exception(_caught_exception(unrelated)) is False


def test_exception_without_traceback_is_not_local_processing() -> None:
    assert _is_local_processing_exception(RuntimeError("detached")) is False
