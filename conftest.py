import logging

import pytest

import tracing


@pytest.fixture(autouse=True)
def _isolate_logging_and_request_id():
    """Keep global logging + request-id state from leaking between tests.

    configure_logging() clears root handlers and the request id is a contextvar;
    save/restore around every test so caplog-based tests stay independent.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    tracing.set_request_id(None)
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    tracing.set_request_id(None)
