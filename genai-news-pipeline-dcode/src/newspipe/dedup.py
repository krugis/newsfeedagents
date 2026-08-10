"""Deduplication v1: exact-match only (no embeddings).

Every unattached arrival is matched to an existing story by:
1. ``url_canonical`` first, then
2. ``title_hash`` within a 72h window of ``stories.first_seen_at``.

Match  -> attach ``arrival.story_id``, increment ``arrival_count``, update
          ``last_seen_at``, set ``hn_front_page`` if the arrival was on the
          HN front page.
No match -> create a new story (``arrival_count = 1``).

Race safety: the whole pass runs inside one transaction holding a Postgres
advisory xact lock with a fixed key, so concurrent dedup invocations
serialize. Fetch runs don't write ``stories``, so they can't race with this.
A global lock is cheap at hourly cadence and simpler than per-row upsert
retry loops, which is why it is preferred here.
"""

from __future__ import annotations

from sqlalchemy import text

from newspipe.db.engine import get_engine
from newspipe.normalize import canonicalize_url, title_hash

DEDUP_LOCK_KEY = 893746021
MATCH_WINDOW_HOURS = 72

_SELECT_UNATTACHED = text(
    """
    SELECT arrival_id, source_id, external_id, url, url_canonical, title, raw
    FROM arrivals
    WHERE story_id IS NULL
    ORDER BY arrival_id
    """
)
_MATCH_BY_CANONICAL = text(
    "SELECT story_id FROM stories WHERE canonical_url = :c ORDER BY story_id LIMIT 1"
)
_MATCH_BY_TITLE = text(
    """
    SELECT story_id FROM stories
    WHERE title_hash = :h
      AND first_seen_at >= now() - make_interval(hours => :window_hours)
    ORDER BY story_id
    LIMIT 1
    """
)
_INSERT_STORY = text(
    """
    INSERT INTO stories (canonical_url, title, title_hash, first_seen_at, last_seen_at,
                         arrival_count, hn_front_page)
    VALUES (:c, :t, :h, now(), now(), 1, :f)
    RETURNING story_id
    """
)
_ATTACH_ARRIVAL = text(
    "UPDATE arrivals SET story_id = :sid WHERE arrival_id = :aid"
)
_UPDATE_STORY = text(
    """
    UPDATE stories
    SET arrival_count = arrival_count + 1,
        last_seen_at = now(),
        hn_front_page = hn_front_page OR :f
    WHERE story_id = :sid
    """
)


def run_dedup() -> dict:
    """Storify all unattached arrivals; returns run stats."""
    stats = {
        "arrivals_processed": 0,
        "stories_created": 0,
        "stories_updated": 0,
        "attachments": 0,
        "errors": 0,
        "affected_story_ids": [],
    }
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DEDUP_LOCK_KEY})
        arrivals = conn.execute(_SELECT_UNATTACHED).mappings().fetchall()

        for arrival in arrivals:
            stats["arrivals_processed"] += 1
            try:
                canonical = canonicalize_url(arrival["url"])
                if arrival["url_canonical"] != canonical:
                    conn.execute(
                        text("UPDATE arrivals SET url_canonical = :c WHERE arrival_id = :id"),
                        {"c": canonical, "id": arrival["arrival_id"]},
                    )
                digest = title_hash(arrival["title"])
                is_front = bool(arrival["raw"].get("hn_front_page")) if arrival["raw"] else False

                story_id = _match_story(conn, canonical, digest)
                if story_id is None:
                    story_id = conn.execute(
                        _INSERT_STORY,
                        {"c": canonical, "t": arrival["title"], "h": digest, "f": is_front},
                    ).scalar_one()
                    stats["stories_created"] += 1
                else:
                    conn.execute(
                        _UPDATE_STORY, {"sid": story_id, "f": is_front}
                    )
                    stats["stories_updated"] += 1

                conn.execute(_ATTACH_ARRIVAL, {"sid": story_id, "aid": arrival["arrival_id"]})
                stats["attachments"] += 1
                stats["affected_story_ids"].append(int(story_id))
            except Exception:  # noqa: BLE001 - one bad arrival must not abort the pass
                stats["errors"] += 1
    return stats


def _match_story(conn, canonical: str, digest: str) -> int | None:
    row = conn.execute(_MATCH_BY_CANONICAL, {"c": canonical}).mappings().fetchone()
    if row is not None:
        return int(row["story_id"])
    row = conn.execute(
        _MATCH_BY_TITLE, {"h": digest, "window_hours": MATCH_WINDOW_HOURS}
    ).mappings().fetchone()
    return int(row["story_id"]) if row is not None else None
