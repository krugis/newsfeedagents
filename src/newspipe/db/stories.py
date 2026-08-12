"""Story-centric read queries spanning stories + labels + sources.

Used by the public front page (web/frontpage.py) to show "top news of the
day": labeled, GenAI/ML-relevant stories for a given day, ranked by hotness
then importance.
"""

from __future__ import annotations

from datetime import date, datetime

import psycopg

_LATEST_LABEL_CTE = """
    WITH latest_labels AS (
        SELECT DISTINCT ON (story_id)
               story_id, is_hot, importance, category, rationale, is_genai_ml_relevant
          FROM labels
         ORDER BY story_id, labeled_at DESC
    )
"""


def select_top_stories(
    conn: psycopg.Connection, start: datetime, end: datetime, limit: int
) -> list[dict]:
    """Labeled, GenAI/ML-relevant stories first seen in `[start, end)`.

    Ranked hottest-and-most-important first. Each story's *latest* label is
    used (a story can in principle be relabeled), joined to its source names.
    """
    return conn.execute(
        _LATEST_LABEL_CTE
        + """
        SELECT s.story_id, s.title, s.canonical_url, s.first_seen_at, s.arrival_count,
               s.hn_front_page, ll.is_hot, ll.importance, ll.category, ll.rationale,
               array_agg(DISTINCT src.name ORDER BY src.name) AS sources
          FROM stories s
          JOIN latest_labels ll ON ll.story_id = s.story_id
          JOIN arrivals a ON a.story_id = s.story_id
          JOIN sources src ON src.source_id = a.source_id
         WHERE ll.is_genai_ml_relevant
           AND s.first_seen_at >= %s AND s.first_seen_at < %s
         GROUP BY s.story_id, ll.is_hot, ll.importance, ll.category, ll.rationale
         ORDER BY ll.is_hot DESC, ll.importance DESC, s.first_seen_at DESC
         LIMIT %s
        """,
        (start, end, limit),
    ).fetchall()


def select_most_recent_labeled_day(
    conn: psycopg.Connection, since: datetime | None = None
) -> date | None:
    """The most recent UTC calendar day with any labeled, relevant story.

    Used to keep the front page non-empty on a quiet day (before today has
    any labeled stories yet). `since` optionally bounds the search (e.g. to
    the selectable day-picker window) so the fallback never reaches further
    back than what's actually pickable.
    """
    sql = (
        _LATEST_LABEL_CTE
        + """
        SELECT (s.first_seen_at AT TIME ZONE 'UTC')::date AS day
          FROM stories s
          JOIN latest_labels ll ON ll.story_id = s.story_id
         WHERE ll.is_genai_ml_relevant
        """
    )
    params: list[datetime] = []
    if since is not None:
        sql += " AND s.first_seen_at >= %s"
        params.append(since)
    sql += " ORDER BY s.first_seen_at DESC LIMIT 1"
    row = conn.execute(sql, tuple(params)).fetchone()
    return row["day"] if row else None
