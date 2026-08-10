"""Operational status CLI (Gate 1.5): last runs, backlog, source health, error tail."""

from __future__ import annotations

from datetime import UTC, datetime

from newspipe.db.engine import connect


def _recent_runs(conn, limit: int = 5) -> list[dict]:
    return conn.execute(
        """
        SELECT run_id, thread_id, started_at, finished_at, status,
               stats->>'sources_ok'      AS sources_ok,
               stats->>'sources_failed'  AS sources_failed,
               stats->>'new_arrivals'    AS new_arrivals,
               stats->>'stories_created' AS stories_created,
               stats->>'labeled'         AS labeled,
               stats->>'duration_s'      AS duration_s
          FROM pipeline_runs
         ORDER BY run_id DESC
         LIMIT %s
        """,
        (limit,),
    ).fetchall()


def _unlabeled_backlog(conn) -> int:
    return conn.execute(
        """
        SELECT count(*) AS n
          FROM stories s LEFT JOIN labels l USING (story_id)
         WHERE l.label_id IS NULL
        """
    ).fetchone()["n"]


def _source_status(conn) -> list[dict]:
    return conn.execute(
        "SELECT name, method, enabled, last_polled_at FROM sources ORDER BY name"
    ).fetchall()


def _recent_errors(conn, limit: int = 5) -> list[dict]:
    return conn.execute(
        """
        SELECT r.run_id, r.thread_id, error.value AS error
          FROM pipeline_runs r,
               jsonb_array_elements_text(r.stats->'errors') AS error
         ORDER BY r.run_id DESC
         LIMIT %s
        """,
        (limit,),
    ).fetchall()


def main() -> int:
    """CLI: print a compact status overview."""
    with connect() as conn:
        runs = _recent_runs(conn)
        backlog = _unlabeled_backlog(conn)
        sources = _source_status(conn)
        errors = _recent_errors(conn)

    print(f"unlabeled backlog: {backlog} stories\n")
    print("last runs:")
    if not runs:
        print("  (none yet)")
    for r in runs:
        print(
            f"  #{r['run_id']:<4} {r['thread_id']:<16} {r['status']:<8} "
            f"ok={r['sources_ok'] or 0} err={r['sources_failed'] or 0} "
            f"new={r['new_arrivals'] or 0} stories={r['stories_created'] or 0} "
            f"labeled={r['labeled'] or 0} {r['duration_s'] or '-'}s "
            f"{r['finished_at'] or '(unfinished)'}"
        )
    print("\nsources:")
    now = datetime.now(UTC)
    for s in sources:
        last = s["last_polled_at"]
        age = "never" if last is None else f"{(now - last).total_seconds() / 60:.0f}min ago"
        enabled = "on" if s["enabled"] else "OFF"
        print(f"  {s['name']:<24} {s['method']:<14} {enabled}  last={age}")
    print("\nrecent errors:")
    if not errors:
        print("  (none)")
    for e in errors:
        print(f"  #{e['run_id']} {e['thread_id']}: {e['error']}")
    return 0
