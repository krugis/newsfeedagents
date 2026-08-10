"""Logging formatter tests."""

from __future__ import annotations

import json
import logging

from newspipe.logging_config import JsonFormatter


def _record(msg: str, **fields) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, None, None)
    if fields:
        record.json_fields = fields
    return record


def test_formatter_emits_valid_json_line() -> None:
    formatter = JsonFormatter()
    line = formatter.format(_record("hello"))
    payload = json.loads(line)
    assert payload["msg"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert "ts" in payload


def test_formatter_includes_extra_fields() -> None:
    formatter = JsonFormatter()
    line = formatter.format(_record("run done", thread_id="run-20260809-05", labeled=100))
    payload = json.loads(line)
    assert payload["thread_id"] == "run-20260809-05"
    assert payload["labeled"] == 100


def test_formatter_includes_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", None, None)
        record.exc_info = __import__("sys").exc_info()
    payload = json.loads(formatter.format(record))
    assert "exc" in payload
    assert "boom" in payload["exc"]
