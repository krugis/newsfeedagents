"""Structured JSON logging: stdout + rotating file in logs/.

JSON lines to stdout at INFO; same format to a rotating file
(``logs/newspipe.log``, 10 MB x 5 backups). Per-run summaries go out at
INFO, per-source detail at DEBUG. Callers attach extra fields via
``extra={"json_fields": {...}}``.
"""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, including any ``json_fields`` extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "json_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with JSON stdout + rotating file handlers."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = JsonFormatter()

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)
    root.addHandler(stdout)

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / "newspipe.log", maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
