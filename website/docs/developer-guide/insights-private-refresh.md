# Bounded private Insights refresh

`hermes state-db insights-refresh-stage` builds a new metadata-only private
stage. It does not publish or replace a summary. Safe-read admission, schema
pinning and descriptor-bound path guards still apply.

Budgets are explicit, positive and finite:

| Flag | Default | Maximum |
| --- | --- | --- |
| `--max-rows` | 10,000 | 2,000,000 |
| `--max-bytes` | 8 MiB | 512 MiB |
| `--seconds` | 5 | 300 |

Rows count sessions plus messages, not sessions alone. The byte limit applies
separately to JSON-encoded metadata projection bytes and the serialized SQLite
artifact. Defaults intentionally refuse large stores; opt in with measured
budgets. Zero, NaN, infinity and values above the maxima are not unlimited modes.

The producer retains its in-memory SQLite implementation. SQLite pages, indexes,
temporary aggregation storage and the serialized copy can coexist; projection
bytes are **not** a RAM bound. The deadline is cooperative (SQLite progress and
row checks, plus a check after serialization), not a preemptive wall timer.
Run large refreshes as dedicated processes under OS address-space, CPU, file-size
and outer wall-time limits. Do not impose process-wide resource limits inside a
shared gateway. A Linux disposable test exercises 12,416 sessions and 683,835
messages with 1 GiB address space, 60 CPU seconds, 256 MiB per-file and a 90-second
outer timeout; a 64 MiB address-space run must refuse without leaving a stage.
These fixture measurements do not guarantee production timing or capacity.

An example explicit capacity selection is `--max-rows 1000000 --max-bytes
268435456 --seconds 120`, after separately applying appropriate OS limits.
Obtain the exact source-schema fingerprint through the supported state-db
interface; never guess it or disable source admission. The stage parent must
satisfy the existing private-directory contract.

Budget exhaustion, SQLite allocation refusal and Python `MemoryError` return a
nonzero generic refusal with no success receipt. Failures before file creation
remove the owned empty stage. As before, an I/O failure during output or a forced
OS kill may leave an incomplete private artifact for inspection; no artifact is
promoted automatically, and cleanup never traverses replacement paths.
