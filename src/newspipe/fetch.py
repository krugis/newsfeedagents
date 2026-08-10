"""`fetch` command: run every due source once and report per-source results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from newspipe.db.arrivals import insert_arrivals
from newspipe.db.engine import connect
from newspipe.db.sources import select_due_sources, update_last_polled
from newspipe.fetchers import get_fetcher


def fetch_all_due(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Fetch every due source; one source's failure never breaks the run.

    Returns {source_name: {"status": "ok"|"error", ...}} for use in run stats.
    """
    now = now or datetime.now(UTC)
    results: dict[str, dict[str, Any]] = {}
    with connect() as conn:
        due = select_due_sources(conn, now)
        for source in due:
            try:
                fetcher = get_fetcher(source.method)
                items = fetcher(source)
                new = insert_arrivals(conn, source.source_id, items)
                update_last_polled(conn, source.source_id, now)
                results[source.name] = {"status": "ok", "fetched": len(items), "new": new}
            except Exception as exc:  # noqa: BLE001 - per-source isolation
                results[source.name] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
    return results


def main() -> int:
    """CLI entry: print per-source fetch counts; exit non-zero if any source failed."""
    results = fetch_all_due()
    if not results:
        print("No sources due (all within poll interval).")
        return 0
    failed = 0
    for name, res in results.items():
        if res["status"] == "ok":
            print(f"  {name:<44} fetched={res['fetched']:>4}  new={res['new']:>4}")
        else:
            failed += 1
            print(f"  {name:<44} ERROR: {res['error']}")
    print(f"({len(results)} sources attempted, {failed} failed)")
    return 1 if failed else 0
