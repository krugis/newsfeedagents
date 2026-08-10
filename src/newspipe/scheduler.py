"""Hourly scheduler (Gate 1.5).

Runs the pipeline graph once per hour at minute 5 via APScheduler. Each tick
invokes the graph under the hour-slot thread_id (`run-YYYYMMDD-HH`), so a
crash that left a checkpoint resumes instead of restarting. `max_instances=1`
prevents overlap; `coalesce=True` and `misfire_grace_time=600` keep late ticks
sane.

Run it with `python -m newspipe scheduler` or via the systemd unit
(`deploy/newspipe.service`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from newspipe.graph.build import log_run, run_pipeline
from newspipe.logging_setup import setup_logging

logger = logging.getLogger(__name__)

MINUTE = "5"  # hourly at minute 5, per spec


def hour_slot_thread_id(now: datetime | None = None) -> str:
    """The checkpoint thread_id for a run started in this hour slot."""
    now = now or datetime.now(UTC)
    return f"run-{now:%Y%m%d-%H}"


def tick() -> None:
    """One scheduled pipeline invocation (safe for APScheduler to call)."""
    thread_id = hour_slot_thread_id()
    logger.info("tick_start", extra={"extra_thread_id": thread_id})
    try:
        result = run_pipeline(thread_id)
    except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the loop
        logger.error(
            "tick_failed",
            extra={"extra_thread_id": thread_id, "extra_error": f"{type(exc).__name__}: {exc}"},
        )
        return
    log_run(result)


def build_scheduler() -> BlockingScheduler:
    """Assemble the scheduler with the pipeline job registered (not started)."""
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        tick,
        CronTrigger(minute=MINUTE),
        id="hourly-pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    return scheduler


def main() -> int:
    """Run the scheduler in the foreground (blocking)."""
    setup_logging(to_stdout=True, level=logging.DEBUG)
    # apscheduler's timezone probe logs at DEBUG; quiet it so the JSON stream
    # carries pipeline records only
    logging.getLogger("tzlocal").setLevel(logging.WARNING)
    scheduler = build_scheduler()
    logger.info("scheduler_started", extra={"extra_jobs": [j.id for j in scheduler.get_jobs()]})
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
