"""Per-request correlation id + timestamped log configuration.

A contextvar holds the current request id; a logging.Filter injects it into
every LogRecord so any log.* call in any module is automatically tagged and
greppable (docker logs | grep <id>). No function signatures need to change.
"""
import contextvars
import logging
import os
import uuid

_request_id = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:6]
    _request_id.set(rid)
    return rid


def set_request_id(rid) -> None:
    _request_id.set(rid or "-")


def get_request_id() -> str:
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record) -> bool:
        record.request_id = _request_id.get()
        return True


def configure_logging() -> None:
    """Install a single stderr handler with timestamp + request id + level.

    LOG_LEVEL (default INFO) controls verbosity. Safe to call multiple times
    (each worker imports the app): existing handlers are cleared first.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
