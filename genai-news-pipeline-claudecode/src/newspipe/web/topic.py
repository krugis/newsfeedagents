"""Public topic search — no login required.

Unlike the front page, this is a keyword search across *all* stories in the
window (labeled or not, relevant-or-not), so an unlabeled match still shows
up (badged "Unlabeled" in the template) instead of waiting for a labeling
run. Defaults to the last `TOPIC_SEARCH_DEFAULT_DAYS` days; `?days=` widens
that up to `TOPIC_SEARCH_MAX_DAYS`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Blueprint, render_template, request

from newspipe.config import get_settings
from newspipe.db.engine import connect
from newspipe.db.pipeline_runs import select_last_successful_run_finished_at
from newspipe.db.stories import select_stories_by_topic

bp = Blueprint("topic", __name__)


def _clamp_days(raw: str | None, *, default: int, maximum: int) -> int:
    """Parse `?days=`; falls back to `default` if absent/malformed, clamped to `[1, maximum]`."""
    if not raw:
        return default
    try:
        days = int(raw)
    except ValueError:
        return default
    return min(max(days, 1), maximum)


@bp.route("/topic")
def topic_search():
    settings = get_settings()
    query = (request.args.get("q") or "").strip()
    days = _clamp_days(
        request.args.get("days"),
        default=settings.topic_search_default_days,
        maximum=settings.topic_search_max_days,
    )

    stories: list[dict] = []
    with connect() as conn:
        if query:
            end = datetime.now(UTC)
            start = end - timedelta(days=days)
            stories = select_stories_by_topic(
                conn, query, start, end, limit=settings.topic_search_limit
            )
        last_updated = select_last_successful_run_finished_at(conn)

    return render_template(
        "topic.html",
        query=query,
        days=days,
        max_days=settings.topic_search_max_days,
        stories=stories,
        last_updated=last_updated,
        searched=bool(query),
    )
