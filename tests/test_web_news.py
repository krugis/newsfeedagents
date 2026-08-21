"""Tests for the paginated /news arrivals browser."""

from __future__ import annotations

import pytest

from newspipe.config import Settings
from newspipe.db.arrivals import insert_arrivals
from newspipe.fetchers.base import RawItem
from newspipe.web.app import create_app


@pytest.fixture
def authed_client(monkeypatch):
    patched = Settings(
        admin_username="tester",
        admin_password="secret123",
        web_session_secret="test-secret",
        admin_login_path="/login",
    )
    monkeypatch.setattr("newspipe.web.app.get_settings", lambda: patched)
    monkeypatch.setattr("newspipe.web.auth.get_settings", lambda: patched)
    app = create_app()
    app.testing = True
    client = app.test_client()
    client.post("/login", data={"username": "tester", "password": "secret123"})
    return client


def test_news_page_shows_seeded_arrivals(db_conn, source_scope, authed_client):
    sid = source_scope("zz-web-news")
    insert_arrivals(
        db_conn,
        sid,
        [
            RawItem(
                external_id=f"zz-news-{i}",
                url=f"https://example.com/zz-news-{i}",
                title=f"Zz News Item {i}",
            )
            for i in range(3)
        ],
    )
    db_conn.commit()

    resp = authed_client.get("/news?page_size=10")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "zz-web-news" in body
    assert "Zz News Item 0" in body


def test_news_page_truncates_long_titles(db_conn, source_scope, authed_client):
    sid = source_scope("zz-web-news-long")
    long_title = "Z" * 150
    insert_arrivals(
        db_conn,
        sid,
        [RawItem(external_id="zz-long-1", url="https://example.com/zz-long", title=long_title)],
    )
    db_conn.commit()

    resp = authed_client.get("/news?page_size=10")

    body = resp.data.decode()
    assert ("Z" * 100 + "…") in body
    assert ("Z" * 101) not in body


def test_news_page_size_falls_back_to_default_when_invalid(authed_client):
    resp = authed_client.get("/news?page_size=999")
    assert resp.status_code == 200
    assert b'value="20" selected' in resp.data


def test_news_page_out_of_range_is_clamped_not_an_error(authed_client):
    resp = authed_client.get("/news?page=99999&page_size=10")
    assert resp.status_code == 200
