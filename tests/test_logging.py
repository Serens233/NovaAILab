"""检查应用和 Uvicorn 的日志边界，不使用真实密钥。"""

import io
import json
import logging

from nova.logging_config import SafeFormatter, configure_logging


def test_logger_level_and_json_format():
    stream = io.StringIO()
    configure_logging("WARNING", stream=stream)
    logger = logging.getLogger("nova.test")
    logger.info("hidden")
    logger.warning("visible")
    payload = json.loads(stream.getvalue())
    assert payload["message"] == "visible"
    assert payload["level"] == "WARNING"
    assert {"timestamp", "logger", "request_id"} <= payload.keys()


def test_known_secret_and_key_value_redaction():
    stream = io.StringIO()
    value = "SYNTHETIC_LOG_SECRET_12345"
    configure_logging("INFO", (value,), stream=stream)
    logging.getLogger("nova.test").info("known=%s token=other-value password=another", value)
    output = stream.getvalue()
    assert value not in output
    assert "other-value" not in output
    assert "another" not in output
    assert "REDACTED" in output


def test_uvicorn_exception_does_not_render_raw_exception_or_chain():
    stream = io.StringIO()
    value = "SYNTHETIC_EXCEPTION_SECRET_987"
    configure_logging("INFO", stream=stream)
    try:
        try:
            raise ValueError(value)
        except ValueError as exc:
            raise RuntimeError(value) from exc
    except RuntimeError:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=True)
    payload = json.loads(stream.getvalue())
    assert value not in stream.getvalue()
    assert payload["exception_type"] == "RuntimeError"
    assert payload["frames"]


def test_uvicorn_access_log_is_disabled():
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    logging.getLogger("uvicorn.access").info("/health?token=untrusted-query")
    assert stream.getvalue() == ""


def test_invalid_log_arguments_do_not_trigger_logging_error_dump():
    record = logging.LogRecord("nova.test", logging.INFO, __file__, 1, "%d", (object(),), None)
    assert json.loads(SafeFormatter().format(record))["message"] == "unformattable_log_event"


def test_reconfiguration_does_not_duplicate_handlers():
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    configure_logging("INFO", stream=stream)
    logging.getLogger("nova.test").info("once")
    assert len(stream.getvalue().splitlines()) == 1


def test_authorization_basic_value_is_fully_redacted():
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    logging.getLogger("nova.test").info("Authorization: Basic SYNTHETIC_BASIC_CREDENTIAL")
    payload = json.loads(stream.getvalue())
    assert "SYNTHETIC_BASIC_CREDENTIAL" not in payload["message"]
    assert "REDACTED" in payload["message"]


def test_quoted_password_with_spaces_is_fully_redacted():
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    logging.getLogger("nova.test").info('password="SYNTHETIC FIRST SECOND"')
    payload = json.loads(stream.getvalue())
    assert "SYNTHETIC" not in payload["message"]
    assert "FIRST" not in payload["message"]
    assert "SECOND" not in payload["message"]
