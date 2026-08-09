"""Scheduler configuration tests (job registration, trigger settings)."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from newspipe.scheduler import build_scheduler


def test_scheduler_registers_one_hourly_job_with_hardening() -> None:
    scheduler = build_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "hourly_pipeline"
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 600
    trigger = job.trigger
    assert isinstance(trigger, CronTrigger)
    minute_field = next(f for f in trigger.fields if f.name == "minute")
    assert minute_field.expressions[0].first == 5
