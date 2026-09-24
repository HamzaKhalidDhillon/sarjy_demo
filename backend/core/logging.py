"""Structured logging with a per-request id and per-stage latency tracking."""
import contextvars
import logging
import time
import uuid
from contextlib import contextmanager

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s [req=%(request_id)s] %(name)s: %(message)s",
    ))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


logger = logging.getLogger("sarjy")


@contextmanager
def timed(stage: str):
    """Log how long a pipeline stage took, tagged with the current request id."""
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("stage=%s ms=%.1f", stage, elapsed_ms)
