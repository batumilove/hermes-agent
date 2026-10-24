"""Regression for host-to-engine snapshot provenance; no live services or LLMs."""
import contextlib
import threading
from types import SimpleNamespace
import pytest
from agent.compression_facade import _run_under_progress_timeout
from agent import conversation_compression


@pytest.mark.parametrize('has_hook',[True,False])
@pytest.mark.parametrize('raises',[True,False])
def test_snapshot_scope_wraps_worker_and_preserves_noop_identity(monkeypatch,has_hook,raises):
    original=[{'role':'user','content':'fixture'}]
    events=[]
    @contextlib.contextmanager
    def scope(source,snapshot):
        assert source is original
        assert snapshot==source and snapshot is not source
        assert snapshot[0] is not source[0]
        events.append('entered')
        try:yield
        finally:events.append('exited')
    engine=SimpleNamespace(native_snapshot_scope=scope) if has_hook else SimpleNamespace()
    def run(fence,*,target_messages,**kwargs):
        assert target_messages == original and target_messages[0] is not original[0]
        assert events == (['entered'] if has_hook else [])
        if raises:raise RuntimeError('fixture failure')
        return target_messages,'fixture prompt'
    monkeypatch.setattr(conversation_compression,'run_compress_context_with_progress_timeout',lambda **kw:kw['worker'](None))
    monkeypatch.setattr(conversation_compression,'request_exceeds_model_window',lambda *a:False)
    def invoke():
        return _run_under_progress_timeout(SimpleNamespace(context_compressor=engine),run,original,'fixture',
            active_fence=None,registration=SimpleNamespace(),fence_registration_lock=threading.Lock(),
            idle_timeout=1,total_ceiling=2)
    if raises:
        with pytest.raises(RuntimeError,match='fixture failure'):invoke()
    else:
        messages,prompt=invoke();assert messages is original and prompt=='fixture prompt'
    assert events == (['entered','exited'] if has_hook else [])
