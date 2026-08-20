"""Tests for the pipeline_state key/value store."""

from __future__ import annotations

from newspipe.db.pipeline_state import get_state, set_state


def test_get_state_missing_key_returns_none(db_conn):
    assert get_state(db_conn, "zz-does-not-exist") is None


def test_set_then_get_roundtrip(db_conn):
    try:
        set_state(db_conn, "zz-test-key", "hello")
        db_conn.commit()
        assert get_state(db_conn, "zz-test-key") == "hello"
    finally:
        db_conn.execute("DELETE FROM pipeline_state WHERE key = 'zz-test-key'")
        db_conn.commit()


def test_set_state_upserts(db_conn):
    try:
        set_state(db_conn, "zz-test-key", "first")
        set_state(db_conn, "zz-test-key", "second")
        db_conn.commit()
        assert get_state(db_conn, "zz-test-key") == "second"
    finally:
        db_conn.execute("DELETE FROM pipeline_state WHERE key = 'zz-test-key'")
        db_conn.commit()
