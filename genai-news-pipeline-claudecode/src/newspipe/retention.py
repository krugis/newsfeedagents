"""Data retention — hard-deletes news older than `Settings.retention_days`.

Opt-in (unset = keep forever). A story is expired once it has had no new
arrival in `retention_days` days (`stories.last_seen_at` ages out); deleting it
cascades by hand (no ON DELETE CASCADE in the schema) in FK-safe order:
labels -> arrivals -> stories. Arrivals that never made it through dedup
(`story_id IS NULL`) are purged separately by their own fetch time so they
don't linger forever as orphans.

Invoked manually (`python -m newspipe retention`) or, when `retention_days`
is set, by a daily scheduler job (see `scheduler.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from newspipe.config import get_settings
from newspipe.db.engine import connect


@dataclass
class RetentionStats:
    """Counters from one retention pass."""

    skipped: bool = False
    dry_run: bool = False
    stories_deleted: int = 0
    arrivals_deleted: int = 0
    labels_deleted: int = 0
    orphan_arrivals_deleted: int = 0


def purge_expired(now: datetime | None = None, dry_run: bool = False) -> RetentionStats:
    """Delete (or, if `dry_run`, just count) news past the retention window.

    No-ops with `skipped=True` when `retention_days` is unset.
    """
    settings = get_settings()
    if not settings.retention_days:
        return RetentionStats(skipped=True, dry_run=dry_run)

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=settings.retention_days)
    stats = RetentionStats(dry_run=dry_run)

    with connect() as conn:
        expired_story_ids = [
            row["story_id"]
            for row in conn.execute(
                "SELECT story_id FROM stories WHERE last_seen_at < %s", (cutoff,)
            ).fetchall()
        ]

        if expired_story_ids:
            stats.labels_deleted = _count_or_delete(
                conn,
                "labels",
                "story_id = ANY(%s)",
                (expired_story_ids,),
                dry_run,
            )
            stats.arrivals_deleted = _count_or_delete(
                conn,
                "arrivals",
                "story_id = ANY(%s)",
                (expired_story_ids,),
                dry_run,
            )
            stats.stories_deleted = _count_or_delete(
                conn,
                "stories",
                "story_id = ANY(%s)",
                (expired_story_ids,),
                dry_run,
            )

        stats.orphan_arrivals_deleted = _count_or_delete(
            conn,
            "arrivals",
            "story_id IS NULL AND fetched_at < %s",
            (cutoff,),
            dry_run,
        )

    return stats


def _count_or_delete(conn, table: str, where: str, params: tuple, dry_run: bool) -> int:
    """Delete matching rows and return the count — or just count them if `dry_run`."""
    if dry_run:
        row = conn.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", params).fetchone()  # noqa: S608
        return row["n"]
    return conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount  # noqa: S608


def main(dry_run: bool = False) -> int:
    """CLI entry: run (or preview) a retention pass and print what happened."""
    settings = get_settings()
    stats = purge_expired(dry_run=dry_run)
    if stats.skipped:
        print("retention_days is not set — skipping (news is kept forever)")
        return 0
    verb = "would delete" if dry_run else "deleted"
    print(f"retention window: {settings.retention_days} days")
    print(f"stories {verb}:          {stats.stories_deleted}")
    print(f"arrivals {verb}:         {stats.arrivals_deleted}")
    print(f"labels {verb}:           {stats.labels_deleted}")
    print(f"orphan arrivals {verb}:  {stats.orphan_arrivals_deleted}")
    return 0
