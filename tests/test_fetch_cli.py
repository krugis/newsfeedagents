"""Fetch runner integration tests against the real DB (network stubbed)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from newspipe.db.engine import get_engine
from newspipe.fetch import run_fetch
from newspipe.models.schemas import RawItem


class _StubFetcher:
    method = "rss"

    def __init__(self, items: list[RawItem], error: Exception | None = None) -> None:
        self._items = items
        self._error = error

    def fetch(self, source) -> list[RawItem]:
        if self._error is not None:
            raise self._error
        return self._items


@pytest.fixture()
def temp_source_id() -> int:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO sources (name, method, config, poll_interval_minutes)
                VALUES ('__test_source__', 'rss', '{}'::jsonb, 0)
                RETURNING source_id
                """
            )
        ).fetchone()
        source_id = int(row[0])
    yield source_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM arrivals WHERE source_id = :sid"), {"sid": source_id})
        conn.execute(text("DELETE FROM sources WHERE source_id = :sid"), {"sid": source_id})


def _stub(items: list[RawItem]) -> _StubFetcher:
    return _StubFetcher(items)


def test_fetch_inserts_arrivals_and_updates_poll_time(
    temp_source_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [RawItem(external_id="x1", url="https://example.com/a", title="A")]
    monkeypatch.setattr("newspipe.fetch.get_fetcher", lambda method: _stub(items))

    stats = run_fetch(source_ids=[temp_source_id])

    assert stats["sources"]["__test_source__"] == {"fetched": 1, "inserted": 1}
    with get_engine().connect() as conn:
        last_polled = conn.execute(
            text("SELECT last_polled_at FROM sources WHERE source_id = :sid"),
            {"sid": temp_source_id},
        ).scalar()
        count = conn.execute(
            text("SELECT count(*) FROM arrivals WHERE source_id = :sid"),
            {"sid": temp_source_id},
        ).scalar()
    assert last_polled is not None
    assert count == 1


def test_fetch_is_idempotent(temp_source_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    items = [RawItem(external_id="x1", url="https://example.com/a", title="A")]
    monkeypatch.setattr("newspipe.fetch.get_fetcher", lambda method: _stub(items))

    run_fetch(source_ids=[temp_source_id])
    stats = run_fetch(source_ids=[temp_source_id])

    assert stats["sources"]["__test_source__"] == {"fetched": 1, "inserted": 0}
    with get_engine().connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM arrivals WHERE source_id = :sid"),
            {"sid": temp_source_id},
        ).scalar()
    assert count == 1


def test_failing_source_recorded_and_does_not_abort(
    temp_source_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    boom = RuntimeError("feed down")
    monkeypatch.setattr(
        "newspipe.fetch.get_fetcher", lambda method: _StubFetcher([], error=boom)
    )

    stats = run_fetch(source_ids=[temp_source_id])

    assert stats["errors"]["__test_source__"] == "feed down"
    with get_engine().connect() as conn:
        last_polled = conn.execute(
            text("SELECT last_polled_at FROM sources WHERE source_id = :sid"),
            {"sid": temp_source_id},
        ).scalar()
    assert last_polled is None  # failed source keeps its old poll time


def test_not_due_source_is_skipped(temp_source_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE sources SET last_polled_at = now(), poll_interval_minutes = 60"
                " WHERE source_id = :sid"
            ),
            {"sid": temp_source_id},
        )
    items = [RawItem(external_id="x2", url="https://example.com/b", title="B")]
    monkeypatch.setattr("newspipe.fetch.get_fetcher", lambda method: _stub(items))

    stats = run_fetch(source_ids=[temp_source_id])

    assert stats["sources"] == {}
    assert stats["errors"] == {}
