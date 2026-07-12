import logging

import tracing


def test_new_request_id_sets_contextvar():
    rid = tracing.new_request_id()
    assert rid and rid == tracing.get_request_id()
    assert len(rid) <= 8


def test_set_request_id_and_default():
    tracing.set_request_id("abcd")
    assert tracing.get_request_id() == "abcd"
    tracing.set_request_id(None)
    assert tracing.get_request_id() == "-"


def test_filter_injects_request_id():
    tracing.set_request_id("zz99")
    rec = logging.LogRecord("n", logging.INFO, __file__, 1, "msg", None, None)
    assert tracing.RequestIdFilter().filter(rec) is True
    assert rec.request_id == "zz99"


def test_configure_logging_format_has_timestamp_and_id(capsys):
    tracing.configure_logging()
    tracing.set_request_id("f00d")
    logging.getLogger("demo").info("hello")
    err = capsys.readouterr().err
    assert "[f00d]" in err
    assert "hello" in err
    # ISO-ish date: YYYY-MM-DD present
    assert err.count("-") >= 2
