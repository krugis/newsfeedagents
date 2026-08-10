"""Structured JSON-lines logging (Gate 1.5).

Every record is emitted as a single JSON line with a UTC timestamp, level,
logger name, message, and any `extra_*` fields the caller attached (used to
carry run_id, thread_id, per-source counts, etc.). A stdout handler is only
attached when requested — the scheduler emits JSON to stdout, while the
interactive CLIs keep stdout for human output and log JSON to the file only.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path

LOGS_DIR = Path("logs")
LOG_FILE = LOGS_DIR / "pipeline.log"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5


class JsonFormatter(logging.Formatter):
    """Format a log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("extra_"):
                entry[key[len("extra_") :]] = value
        return json.dumps(entry, default=str)


def setup_logging(*, to_stdout: bool, level: int = logging.INFO) -> None:
    """Configure root logging: rotating JSON file, optionally JSON to stdout.

    Idempotent — repeated calls keep one file handler and one stdout handler.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        LOGS_DIR.mkdir(exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
    if to_stdout and not any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stdout for h in root.handlers
    ):
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(JsonFormatter())
        root.addHandler(stdout_handler)
