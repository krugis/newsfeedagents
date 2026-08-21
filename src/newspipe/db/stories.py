"""Story-centric read queries spanning stories + labels + sources.

Used by the public front page (web/frontpage.py) to show "top news of the
day": labeled, GenAI/ML-relevant stories for a given day, ranked by hotness
then importance. Also backs topic search (web/topic.py, telegram_bot/bot.py),
which searches titles across *all* stories — labeled or not.
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


_TOPIC_SELECT_COLUMNS = """
        SELECT s.story_id, s.title, s.canonical_url, s.first_seen_at, s.arrival_count,
               s.hn_front_page, ll.is_hot, ll.importance, ll.category, ll.rationale,
               array_agg(DISTINCT src.name ORDER BY src.name) AS sources,
"""

_TOPIC_FROM_WHERE = """
          FROM stories s
          LEFT JOIN latest_labels ll ON ll.story_id = s.story_id
          JOIN arrivals a ON a.story_id = s.story_id
          JOIN sources src ON src.source_id = a.source_id
         WHERE {match}
           AND s.first_seen_at >= %s AND s.first_seen_at < %s
         GROUP BY s.story_id, ll.is_hot, ll.importance, ll.category, ll.rationale
         ORDER BY rank DESC, ll.importance DESC NULLS LAST, ll.is_hot DESC NULLS LAST,
                  s.first_seen_at DESC
         LIMIT %s
"""

# Trigram similarity below this is treated as noise, not a fuzzy match — chosen
# to still catch a one-typo miss on a short word (e.g. "andropic" ~ "Anthropic")
# without matching on mere coincidental letter overlap.
_FUZZY_SIMILARITY_THRESHOLD = 0.3


def select_stories_by_topic(
    conn: psycopg.Connection, query: str, start: datetime, end: datetime, limit: int
) -> list[dict]:
    """Stories whose title matches `query`, first seen in `[start, end)`, ranked by relevance.

    Unlike `select_top_stories`, this is not restricted to labeled or
    GenAI/ML-relevant stories — a story with no `labels` row yet still
    matches, with `is_hot`/`importance`/`category`/`rationale` all `None`
    (callers show these as "unlabeled").

    Two-tier matching: full-text search (`websearch_to_tsquery` against a
    generated `tsvector` column, ranked by `ts_rank`) is tried first, since
    it properly weighs term frequency instead of just "does the substring
    appear". If that finds nothing — e.g. `query` is a typo, or is short
    enough that `to_tsquery` doesn't stem it usefully — a trigram-similarity
    fuzzy fallback (`pg_trgm`'s `word_similarity`) catches near-misses.
    Within each tier, ties break the same way as `select_top_stories`:
    importance/hotness first, then newest.
    """
    rows = conn.execute(
        _LATEST_LABEL_CTE
        + _TOPIC_SELECT_COLUMNS
        + "       ts_rank(s.title_tsv, websearch_to_tsquery('english', %s)) AS rank\n"
        + _TOPIC_FROM_WHERE.format(match="s.title_tsv @@ websearch_to_tsquery('english', %s)"),
        (query, query, start, end, limit),
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        _LATEST_LABEL_CTE
        + _TOPIC_SELECT_COLUMNS
        + "       word_similarity(%s, s.title) AS rank\n"
        + _TOPIC_FROM_WHERE.format(match="word_similarity(%s, s.title) > %s"),
        (query, query, _FUZZY_SIMILARITY_THRESHOLD, start, end, limit),
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
