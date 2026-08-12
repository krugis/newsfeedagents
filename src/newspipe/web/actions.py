"""Runs the fetch/dedup/label steps on demand, triggered from the admin page.

A single in-process lock keeps two triggers (e.g. a double-click) from
running concurrently — this is a single-operator internal tool served by one
process, so a `threading.Lock` is enough; no cross-process coordination.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from time import monotonic
from typing import Literal

from newspipe import dedup, fetch
from newspipe.config import get_settings
from newspipe.labeling.labeler import label_unlabeled

ActionName = Literal["fetch", "dedup", "label"]

_lock = threading.Lock()


class ActionBusyError(RuntimeError):
    """Raised when an action is requested while another is already running."""


def run_action(name: ActionName) -> dict:
    """Run one pipeline step and return a stats dict, or raise ActionBusyError."""
    if not _lock.acquire(blocking=False):
        raise ActionBusyError("another action is already running — try again shortly")
    started = monotonic()
    try:
        if name == "fetch":
            stats: dict = {"per_source": fetch.fetch_all_due()}
        elif name == "dedup":
            stats = asdict(dedup.run_dedup())
        elif name == "label":
            settings = get_settings()
            stats = asdict(label_unlabeled(limit=settings.label_limit_per_run))
        else:  # pragma: no cover - guarded by the route's URL converter
            raise ValueError(f"unknown action: {name!r}")
    finally:
        _lock.release()
    stats["duration_s"] = round(monotonic() - started, 3)
    return stats
