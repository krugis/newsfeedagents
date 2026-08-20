"""Public "top news of the day" front page — no login required.

Unlike /admin and /news, this is the point of the whole pipeline: a
newspaper-style digest of a day's labeled, GenAI/ML-relevant stories,
hottest and most important first. The last 7 days (newest first) are
selectable via `?day=YYYY-MM-DD`; days outside that window are ignored.
With no `day` given, today is shown, falling back to the most recent day
within the window that has any stories, so the page is never blank early
in the day before anything's labeled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from flask import Blueprint, render_template, request

from newspipe.db.engine import connect
from newspipe.db.stories import select_most_recent_labeled_day, select_top_stories

bp = Blueprint("frontpage", __name__)

TOP_STORIES_LIMIT = 20
DAY_WINDOW = 7  # "last 7 days", selectable, newest first


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _parse_day(raw: str | None, *, today: date, earliest: date) -> date | None:
    """Parse `?day=YYYY-MM-DD`; None if absent, malformed, or outside the window."""
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed if earliest <= parsed <= today else None


@bp.route("/")
def frontpage():
    today = datetime.now(UTC).date()
    earliest = today - timedelta(days=DAY_WINDOW - 1)
    selectable_days = [today - timedelta(days=n) for n in range(DAY_WINDOW)]  # newest first

    requested = _parse_day(request.args.get("day"), today=today, earliest=earliest)
    shown_day = requested if requested is not None else today
    used_fallback = False

    with connect() as conn:
        start, end = _day_bounds(shown_day)
        stories = select_top_stories(conn, start, end, limit=TOP_STORIES_LIMIT)
        if requested is None and not stories:
            fallback_day = select_most_recent_labeled_day(conn, since=_day_bounds(earliest)[0])
            if fallback_day is not None and fallback_day != shown_day:
                shown_day = fallback_day
                start, end = _day_bounds(shown_day)
                stories = select_top_stories(conn, start, end, limit=TOP_STORIES_LIMIT)
                used_fallback = True

    lead, rest = (stories[0], stories[1:]) if stories else (None, [])
    return render_template(
        "frontpage.html",
        lead=lead,
        rest=rest,
        shown_day=shown_day,
        selectable_days=selectable_days,
        used_fallback=used_fallback,
    )
