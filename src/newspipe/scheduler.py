"""APScheduler hourly scheduling of the pipeline graph.

BlockingScheduler with a cron trigger at minute 5 (UTC),
``max_instances=1``, ``coalesce=True``, ``misfire_grace_time=600`` so two
runs can never overlap and missed runs coalesce into one. Each tick invokes
the graph with the hour-slot thread id.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from newspipe.config import get_settings
from newspipe.graph.build import build_graph, hour_thread_id
from newspipe.graph.state import INITIAL_STATE

logger = logging.getLogger(__name__)


def run_scheduled_once() -> dict | None:
    """Run the pipeline for the current hour slot; logs a JSON summary."""
    thread_id = hour_thread_id()
    logger.info(
        "scheduled run starting",
        extra={
            "json_fields": {
                "thread_id": thread_id,
                "label_limit": get_settings().label_limit_per_run,
            }
        },
    )
    graph = build_graph()
    try:
        result = graph.invoke(INITIAL_STATE, config={"configurable": {"thread_id": thread_id}})
    except Exception:  # noqa: BLE001 - scheduler must survive a bad run
        logger.exception(
            "scheduled run failed", extra={"json_fields": {"thread_id": thread_id}}
        )
        return None
    stats = result.get("stats", {})
    logger.info(
        "scheduled run complete",
        extra={
            "json_fields": {
                "thread_id": thread_id,
                "status": stats.get("status"),
                "new_stories": stats.get("new_stories", 0),
                "stories_updated": stats.get("stories_updated", 0),
                "labeled": stats.get("labeled", 0),
                "error_count": stats.get("error_count", 0),
                "duration_s": stats.get("duration_seconds"),
            }
        },
    )
    for name, source_stats in sorted((stats.get("sources") or {}).items()):
        logger.debug(
            "source detail",
            extra={"json_fields": {"source": name, **source_stats}},
        )
    return result


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled_once,
        CronTrigger(minute=5, timezone="UTC"),
        id="hourly_pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    return scheduler


def main() -> None:
    from newspipe.logging_config import configure_logging

    configure_logging(logging.INFO)
    scheduler = build_scheduler()
    logger.info(
        "scheduler started (cron minute 5 hourly, max_instances=1, coalesce, misfire_grace 600s)"
    )
    scheduler.start()
