"""Persistence for story labels (`labels` table).

Stories live one-to-many with labels so relabeling / evaluation runs can be
added later without touching `stories`. A story is "unlabeled" when it has no
`labels` row yet; `select_unlabeled_stories` returns everything the prompt
needs in one query.
"""

from __future__ import annotations


def select_unlabeled_stories(
    conn, limit: int | None = None, story_ids: list[int] | None = None
) -> list[dict]:
    """Return unlabeled stories, oldest first.

    Each row carries the joined source names (an explicit importance signal),
    `arrival_count`, and `hn_front_page`. `limit` caps the batch; `story_ids`
    restricts to a specific set (used by tests and targeted relabeling).
    `LIMIT NULL` means no limit, so `limit=None` labels everything.
    """
    sql = """
        SELECT s.story_id,
               s.title,
               s.arrival_count,
               s.hn_front_page,
               array_agg(DISTINCT src.name ORDER BY src.name) AS sources
          FROM stories s
          JOIN arrivals a ON a.story_id = s.story_id
          JOIN sources src ON src.source_id = a.source_id
          LEFT JOIN labels l ON l.story_id = s.story_id
         WHERE l.label_id IS NULL
    """
    params: list[object] = []
    if story_ids:
        sql += " AND s.story_id = ANY(%s)"
        params.append(story_ids)
    sql += """
         GROUP BY s.story_id
         ORDER BY s.first_seen_at, s.story_id
         LIMIT %s
    """
    params.append(limit)
    return conn.execute(sql, tuple(params)).fetchall()


def select_unlabeled_story_sources(conn) -> list[dict]:
    """For every unlabeled story: its "primary" source and first_seen_at.

    The primary source is whichever source's arrival for this story has the
    earliest `fetched_at` — i.e. whichever source broke it first. Used to
    fairly round-robin the labeling budget across sources instead of letting
    one prolific source's stories dominate a run (see labeling/labeler.py).
    """
    return conn.execute(
        """
        SELECT DISTINCT ON (s.story_id)
               s.story_id, a.source_id, s.first_seen_at
          FROM stories s
          JOIN arrivals a ON a.story_id = s.story_id
          LEFT JOIN labels l ON l.story_id = s.story_id
         WHERE l.label_id IS NULL
         ORDER BY s.story_id, a.fetched_at ASC
        """
    ).fetchall()


def insert_label(
    conn,
    story_id: int,
    *,
    is_hot: bool,
    importance: int,
    category: str,
    is_genai_ml_relevant: bool,
    rationale: str | None,
    model: str | None,
    prompt_version: str | None,
) -> int:
    """Persist one label for one story; returns the new `label_id`."""
    row = conn.execute(
        """
        INSERT INTO labels
            (story_id, is_hot, importance, category, is_genai_ml_relevant,
             rationale, model, prompt_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING label_id
        """,
        (
            story_id,
            is_hot,
            importance,
            category,
            is_genai_ml_relevant,
            rationale,
            model,
            prompt_version,
        ),
    ).fetchone()
    return row["label_id"]
