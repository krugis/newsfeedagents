"""Tests for the hourly scheduler (Sub-phase 1.5)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from newspipe.config import Settings
from newspipe.logging_setup import JsonFormatter
from newspipe.scheduler import build_scheduler, hour_slot_thread_id


def test_hour_slot_thread_id():
    now = datetime(2026, 8, 10, 14, 5, tzinfo=UTC)
    assert hour_slot_thread_id(now) == "run-20260810-14"


def test_scheduler_job_config():
    scheduler = build_scheduler()
    job = scheduler.get_job("hourly-pipeline")
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 600
    # fires every hour at minute 5 (default SCHEDULER_CRON_MINUTE/HOUR)
    nxt = job.trigger.get_next_fire_time(None, datetime.now(UTC))
    assert nxt is not None
    assert nxt.minute == 5


def test_scheduler_job_config_custom_cron(monkeypatch):
    patched = Settings(scheduler_cron_minute="*/15", scheduler_cron_hour="*")
    monkeypatch.setattr("newspipe.scheduler.get_settings", lambda: patched)
    scheduler = build_scheduler()
    job = scheduler.get_job("hourly-pipeline")
    nxt = job.trigger.get_next_fire_time(None, datetime.now(UTC))
    assert nxt is not None
    assert nxt.minute % 15 == 0


def test_scheduler_no_retention_job_by_default():
    scheduler = build_scheduler()
    assert scheduler.get_job("daily-retention") is None


def test_scheduler_adds_retention_job_when_configured(monkeypatch):
    patched = Settings(retention_days=30)
    monkeypatch.setattr("newspipe.scheduler.get_settings", lambda: patched)
    scheduler = build_scheduler()
    job = scheduler.get_job("daily-retention")
    assert job is not None
    assert job.max_instances == 1


def test_json_formatter_emits_json_with_extras():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "newspipe.test", logging.INFO, "m.py", 1, "run_completed", None, None
    )
    record.extra_run_id = 7
    record.extra_thread_id = "run-20260810-14"
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "run_completed"
    assert parsed["run_id"] == 7
    assert parsed["thread_id"] == "run-20260810-14"
    assert parsed["ts"]


def test_setup_logging_writes_rotating_json_file(tmp_path, monkeypatch):
    from newspipe import logging_setup as ls

    monkeypatch.setattr(ls, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(ls, "LOG_FILE", tmp_path / "pipeline.log")
    ls.setup_logging(to_stdout=False)
    logging.getLogger("newspipe.test2").info("hello", extra={"extra_k": "v"})
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = (tmp_path / "pipeline.log").read_text().strip().splitlines()
    assert lines
    assert json.loads(lines[0])["message"] == "hello"
