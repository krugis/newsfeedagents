"""Public "top news of the day" front page — no login required.

Unlike /admin and /news, this is the point of the whole pipeline: a
newspaper-style digest of today's labeled, GenAI/ML-relevant stories,
hottest and most important first. Falls back to the most recent day that has
any, so the page is never blank early in the day before anything's labeled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from flask import Blueprint, render_template

from newspipe.db.engine import connect
from newspipe.db.stories import select_most_recent_labeled_day, select_top_stories

bp = Blueprint("frontpage", __name__)

TOP_STORIES_LIMIT = 20


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


@bp.route("/")
def frontpage():
    today = datetime.now(UTC).date()
    start, end = _day_bounds(today)
    shown_day = today
    with connect() as conn:
        stories = select_top_stories(conn, start, end, limit=TOP_STORIES_LIMIT)
        if not stories:
            fallback_day = select_most_recent_labeled_day(conn)
            if fallback_day is not None:
                start, end = _day_bounds(fallback_day)
                stories = select_top_stories(conn, start, end, limit=TOP_STORIES_LIMIT)
                shown_day = fallback_day
    lead, rest = (stories[0], stories[1:]) if stories else (None, [])
    return render_template(
        "frontpage.html",
        lead=lead,
        rest=rest,
        shown_day=shown_day,
        is_today=shown_day == today,
    )
