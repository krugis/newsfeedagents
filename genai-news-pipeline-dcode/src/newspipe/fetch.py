"""Fetch runner: ``python -m newspipe fetch`` and per-source helper."""

from __future__ import annotations

from newspipe.db.arrivals import insert_arrivals, update_source_last_polled
from newspipe.db.engine import get_engine
from newspipe.db.sources import select_due_sources
from newspipe.fetchers import get_fetcher
from newspipe.models.schemas import Source


def fetch_one_source(source: Source) -> dict:
    """Fetch one source, persist arrivals, update last_polled_at.

    Runs in its own transaction; raises on failure so callers decide how to
    record the error.
    """
    engine = get_engine()
    with engine.begin() as conn:
        items = get_fetcher(source.method).fetch(source)
        inserted_ids = insert_arrivals(conn, source.source_id, items)
        update_source_last_polled(conn, source.source_id)
    return {"fetched": len(items), "inserted": len(inserted_ids), "arrival_ids": inserted_ids}


def run_fetch(source_ids: list[int] | None = None) -> dict:
    """Poll every due source once; a single failure never aborts the rest.

    Returns per-source counts plus any errors, keyed by source name.
    """
    stats: dict = {"sources": {}, "errors": {}, "total_fetched": 0, "total_inserted": 0}
    engine = get_engine()
    with engine.begin() as conn:
        sources = select_due_sources(conn, source_ids=source_ids)
    for source in sources:
        try:
            res = fetch_one_source(source)
        except Exception as exc:  # noqa: BLE001 - per-source isolation is required
            stats["errors"][source.name] = str(exc)
            stats["sources"][source.name] = {"fetched": 0, "inserted": 0, "error": str(exc)}
            continue
        stats["sources"][source.name] = {"fetched": res["fetched"], "inserted": res["inserted"]}
        stats["total_fetched"] += res["fetched"]
        stats["total_inserted"] += res["inserted"]
    return stats
