"""Tests for the admin page's fetch/dedup/label triggers and busy-lock."""

from __future__ import annotations

import pytest

from newspipe.dedup import DedupStats
from newspipe.labeling.labeler import LabelStats
from newspipe.web import actions


def test_run_action_fetch_calls_fetch_all_due(monkeypatch):
    monkeypatch.setattr(
        "newspipe.fetch.fetch_all_due",
        lambda now=None: {"src-a": {"status": "ok", "fetched": 3, "new": 2}},
    )
    stats = actions.run_action("fetch")
    assert stats["per_source"]["src-a"]["new"] == 2
    assert "duration_s" in stats


def test_run_action_dedup_calls_run_dedup(monkeypatch):
    monkeypatch.setattr(
        "newspipe.dedup.run_dedup",
        lambda now=None: DedupStats(arrivals_processed=5, stories_created=2),
    )
    stats = actions.run_action("dedup")
    assert stats["arrivals_processed"] == 5
    assert stats["stories_created"] == 2


def test_run_action_label_calls_label_unlabeled(monkeypatch):
    monkeypatch.setattr(
        "newspipe.web.actions.label_unlabeled",
        lambda limit=None: LabelStats(stories_attempted=4, labels_created=3),
    )
    stats = actions.run_action("label")
    assert stats["stories_attempted"] == 4
    assert stats["labels_created"] == 3


def test_run_action_raises_busy_when_locked(monkeypatch):
    monkeypatch.setattr("newspipe.dedup.run_dedup", lambda now=None: DedupStats())
    assert actions._lock.acquire(blocking=False)
    try:
        with pytest.raises(actions.ActionBusyError):
            actions.run_action("dedup")
    finally:
        actions._lock.release()
