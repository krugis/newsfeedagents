"""Operational status: ``python -m newspipe status``."""

from __future__ import annotations

from sqlalchemy import text

from newspipe.db.engine import get_engine

_LAST_RUNS = text(
    """
    SELECT run_id, thread_id, status, started_at, finished_at,
           round(extract(epoch FROM (finished_at - started_at))::numeric, 1) AS duration_s,
           (stats->>'new_stories')::int AS new_stories,
           (stats->>'labeled')::int AS labeled,
           (stats->>'error_count')::int AS error_count,
           stats->'errors' AS errors
    FROM pipeline_runs
    ORDER BY run_id DESC
    LIMIT 5
    """
)
_BACKLOG = text(
    """
    SELECT count(*) FROM stories st
    WHERE NOT EXISTS (SELECT 1 FROM labels l WHERE l.story_id = st.story_id)
    """
)
_SOURCES = text(
    """
    SELECT source_id, name, method, enabled, last_polled_at
    FROM sources
    ORDER BY source_id
    """
)


def get_status() -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        runs = conn.execute(_LAST_RUNS).mappings().fetchall()
        backlog = conn.execute(_BACKLOG).scalar()
        sources = conn.execute(_SOURCES).mappings().fetchall()
    return {
        "runs": [dict(row) for row in runs],
        "unlabeled_backlog": int(backlog or 0),
        "sources": [dict(row) for row in sources],
    }


def print_status() -> None:
    status = get_status()
    runs = status["runs"]
    if not runs:
        print("no runs yet")
    else:
        header = (
            f"{'run_id':>6} {'thread_id':<24}{'status':<24}"
            f"{'new':>4}{'lbl':>4}{'err':>4}{'dur':>7}"
        )
        print(header)
        for r in runs:
            dur = r["duration_s"] if r["duration_s"] is not None else "-"
            print(
                f"{r['run_id']:>6} {r['thread_id']:<24}{r['status']:<24}"
                f"{r['new_stories'] or 0:>4}{r['labeled'] or 0:>4}"
                f"{r['error_count'] or 0:>4}{dur:>7}"
            )
        for r in runs:
            if r["errors"]:
                print(f"errors in run {r['run_id']} ({r['thread_id']}):")
                for err in r["errors"]:
                    print(f"  {err}")

    print(f"unlabeled backlog: {status['unlabeled_backlog']}")
    print(f"{'source_id':>9} {'name':<34}{'method':<16}{'enabled':>8}  last_polled_at")
    for s in status["sources"]:
        last = s["last_polled_at"].isoformat() if s["last_polled_at"] else "-"
        print(
            f"{s['source_id']:>9} {s['name']:<34}{s['method']:<16}{str(s['enabled']):>8}  {last}"
        )
