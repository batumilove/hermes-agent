"""Atomic external-worker acknowledgement publication."""
from pathlib import Path
from typing import Callable


def worker_still_owned(execution_id: str, adopted: dict, endpoint: float | None) -> bool:
    """Revalidate the durable owner and shared acknowledgement deadline."""
    from cron import scheduler as s
    from cron.executions import get_execution

    current = get_execution(execution_id)
    return (current is not None and current['status'] == 'running'
            and all(current[k] == adopted[k] for k in
                    ('process_id', 'pid', 'process_started_at'))
            and (endpoint is None or s.time.monotonic() < endpoint))


def publish_worker_ack(ack_path: Path, execution_id: str, still_owned: Callable[[], bool]) -> bool:
    """Publish readiness only while the adopted execution remains owned."""
    from cron import scheduler as s

    os, json, logger = s.os, s.json, s.logger
    try:
        ack_path.parent.mkdir(parents=True, exist_ok=True)
        # Publish via write-to-temp + atomic rename. Writing ack_path in place
        # (the old approach) let O_CREAT make the empty file visible to the
        # scheduler's exists()-then-read polling loop before the JSON body was
        # written, occasionally handing it a 0-byte file and a JSONDecodeError.
        # os.replace() is a single atomic syscall on the same filesystem, so
        # readers only ever see the file fully absent or fully written.
        ack_tmp_path = ack_path.with_name(f"{ack_path.name}.tmp{os.getpid()}")
        fd = os.open(ack_tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as ack_file:
                json.dump({"pid": os.getpid(), "execution_id": execution_id}, ack_file)
                ack_file.flush()
                os.fsync(ack_file.fileno())
            if not still_owned():
                ack_tmp_path.unlink(missing_ok=True)
                return False
            os.replace(ack_tmp_path, ack_path)
        except BaseException:
            ack_tmp_path.unlink(missing_ok=True)
            raise
    except Exception:
        logger.exception(
            "Cron external worker could not publish ready acknowledgement for %s",
            execution_id,
        )
        return False
    return True
