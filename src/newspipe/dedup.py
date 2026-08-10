"""Dedup v1 — exact-match only (no embeddings).

Every unattached arrival is matched to a story by canonical URL first, then by
title-hash within a 72h window of the story's first_seen_at. A match attaches
the arrival and bumps the story's counters; a miss creates a new story. The
URL of each arrival is canonicalized here (backfilling `arrivals.url_canonical`).

Concurrency: the whole run executes inside one transaction guarded by a Postgres
advisory lock, so concurrent dedup runs cannot both create a story for the same
arrival. (The two-key lookup can't be expressed as a pure upsert, so a lock is
the simple, correct choice — dedup is a fast hourly batch, so serializing it is
cheap.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from newspipe.db.engine import connect
from newspipe.normalize import canonicalize_url, normalize_title, title_hash

TITLE_MATCH_WINDOW = timedelta(hours=72)
_ADVISORY_LOCK_KEY = 704001  # arbitrary constant serializing dedup runs

_INSERT_STORY = """
INSERT INTO stories (canonical_url, title, title_hash, first_seen_at, last_seen_at, hn_front_page)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING story_id
"""

_UPDATE_STORY = """
UPDATE stories
   SET arrival_count = arrival_count + %s,
       last_seen_at = GREATEST(last_seen_at, %s),
       hn_front_page = hn_front_page OR %s
 WHERE story_id = %s
"""

_ATTACH_ARRIVAL = "UPDATE arrivals SET url_canonical = %s, story_id = %s WHERE arrival_id = %s"


@dataclass
class DedupStats:
    """Counters from one dedup run."""

    arrivals_processed: int = 0
    stories_created: int = 0
    arrivals_attached: int = 0
    title_matches: int = 0


def run_dedup(now: datetime | None = None) -> DedupStats:
    """Deduplicate all unattached arrivals into stories (idempotent, race-safe)."""
    now = now or datetime.now(UTC)
    stats = DedupStats()
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))

        arrivals = _unattached_arrivals(conn)
        stats.arrivals_processed = len(arrivals)
        if not arrivals:
            return stats

        normalized = _normalize_arrivals(arrivals)
        canon_map = _index(normalized, "canonical")
        title_map = _index(normalized, "title_hash")

        story_by_canon = dict(_existing_stories_by_canon(conn, canon_map))
        story_by_hash = dict(_existing_stories_by_hash(conn, title_map, now))

        attachments: list[tuple] = []
        story_updates: dict[int, dict] = {}

        for entry in normalized:
            story_id, matched = _match(entry, story_by_canon, story_by_hash)

            if story_id is None:
                row = conn.execute(
                    _INSERT_STORY,
                    (
                        entry["canonical"],
                        entry["title"],
                        entry["title_hash"],
                        now,
                        now,
                        entry["hn_front_page"],
                    ),
                ).fetchone()
                story_id = row["story_id"]
                stats.stories_created += 1
                if entry["canonical"]:
                    story_by_canon[entry["canonical"]] = story_id
                story_by_hash.setdefault(entry["title_hash"], story_id)
            else:
                if matched == "title":
                    stats.title_matches += 1
                agg = story_updates.setdefault(story_id, {"count": 0, "hn": False, "last": now})
                agg["count"] += 1
                agg["hn"] = agg["hn"] or entry["hn_front_page"]
                if entry["published_at"] and entry["published_at"] > agg["last"]:
                    agg["last"] = entry["published_at"]

            attachments.append((entry["canonical"], story_id, entry["arrival_id"]))
            stats.arrivals_attached += 1

        for canonical, story_id, arrival_id in attachments:
            conn.execute(_ATTACH_ARRIVAL, (canonical, story_id, arrival_id))
        for story_id, agg in story_updates.items():
            conn.execute(_UPDATE_STORY, (agg["count"], agg["last"], agg["hn"], story_id))

    return stats


def main() -> int:
    """CLI entry: run dedup and print what happened."""
    stats = run_dedup()
    print(f"arrivals processed: {stats.arrivals_processed}")
    print(f"stories created:    {stats.stories_created}")
    print(f"arrivals attached:  {stats.arrivals_attached}")
    print(f"title-hash matches: {stats.title_matches}")
    return 0


def _unattached_arrivals(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT arrival_id, source_id, url, title, published_at, raw
        FROM arrivals
        WHERE story_id IS NULL
        ORDER BY arrival_id
        """
    ).fetchall()


def _normalize_arrivals(arrivals: list[dict]) -> list[dict]:
    normalized = []
    for arrival in arrivals:
        normalized.append(
            {
                "arrival_id": arrival["arrival_id"],
                "canonical": canonicalize_url(arrival["url"]),
                "title": normalize_title(arrival["title"]),
                "title_hash": title_hash(arrival["title"]),
                "published_at": arrival["published_at"],
                "hn_front_page": bool((arrival["raw"] or {}).get("hn_front_page")),
            }
        )
    return normalized


def _index(normalized: list[dict], key: str) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for entry in normalized:
        value = entry[key]
        if value:
            index.setdefault(value, []).append(entry)
    return index


def _existing_stories_by_canon(conn, canon_map: dict[str, list[dict]]) -> dict[str, int]:
    if not canon_map:
        return {}
    rows = conn.execute(
        "SELECT story_id, canonical_url FROM stories WHERE canonical_url = ANY(%s)",
        (list(canon_map),),
    ).fetchall()
    return {row["canonical_url"]: row["story_id"] for row in rows}


def _existing_stories_by_hash(
    conn, title_map: dict[str, list[dict]], now: datetime
) -> dict[str, int]:
    if not title_map:
        return {}
    rows = conn.execute(
        """
        SELECT story_id, title_hash, first_seen_at
        FROM stories
        WHERE title_hash = ANY(%s) AND first_seen_at >= %s
        ORDER BY first_seen_at
        """,
        (list(title_map), now - TITLE_MATCH_WINDOW),
    ).fetchall()
    result: dict[str, int] = {}
    for row in rows:  # oldest story wins when several share a hash in the window
        result.setdefault(row["title_hash"], row["story_id"])
    return result


def _match(entry: dict, story_by_canon: dict, story_by_hash: dict) -> tuple[int | None, str | None]:
    if entry["canonical"] and entry["canonical"] in story_by_canon:
        return story_by_canon[entry["canonical"]], "canonical"
    if entry["title_hash"] in story_by_hash:
        return story_by_hash[entry["title_hash"]], "title"
    return None, None
