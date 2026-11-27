# Isolated stderr logging revision

Scope: candidate worktree only; no publication, deployment, service restart or live writes.
Parent: bd9ad7103d6f02476fa79e5916504d4017d9b20c.
Restart impact: restart_free for candidate preparation; eventual gateway activation is restart_required and not authorized here.
Target processes: isolated pytest and child Python only. Activation grouping: none. Rollback identity: parent SHA above.

## Design

Stderr no longer enters the shared file QueueListener. Each console handler owns an independent daemon writer and a 1024-record bounded queue. Producers shallow-copy records, never format/write/flush the stream. Overflow drops console records silently. Handler close enqueues a sentinel (evicting a queued console record if necessary), waits at most 0.1 seconds, and never flushes the blocked stream. Its worker holds no logging.Handler lock, so stdlib logging.shutdown cannot wait on its stream I/O. File logging retains its existing lifecycle and routing; this patch deliberately does not claim to bound pre-existing file I/O shutdown behavior.

## TDD

Added lifecycle regression first on the rejected parent: verbose registration, file registration+flush, and stop all failed with `lifecycle joined a blocked stderr writer` (3 failed, 8 deselected). After independent stderr queues: focused logging suite 56 passed, 4 skipped. Additional subprocess natural-exit and bounded-queue/close coverage was then added. The subprocess leaves stderr permanently blocked and must exit naturally within 3 seconds.

## Exact verified environment and command

Host: SSH alias `hermes`. Python: `/home/ubuntu/.hermes/hermes-agent/venv/bin/python`, Python 3.11.15, pytest 9.1.1. Do not use system Python or the similarly named `.venv`. No dependency installation or environment activation required. Candidate cwd is essential for source imports.

```sh
ssh hermes 'cd /home/ubuntu/.hermes/worktrees/gateway-async-stderr-20261127 && /home/ubuntu/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_gateway_logging_nonblocking.py tests/test_hermes_logging.py tests/test_log_isolation.py tests/gateway/test_multiplex_log_routing.py tests/hermes_cli/test_profile_delete_log_handlers.py tests/hermes_cli/test_web_logs_async.py -o addopts= -q'
```

Result: **65 passed, 4 skipped in 5.05s**. Skips are platform-specific Windows tests on Linux. `git diff --check` passed. This is focused/regression validation, not a claim that the entire repository suite ran.
