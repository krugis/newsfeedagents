"""Tests for the web UI's session login (single admin account)."""

from __future__ import annotations

import pytest

from newspipe.config import Settings
from newspipe.web.app import create_app


@pytest.fixture
def client(monkeypatch):
    patched = Settings(
        admin_username="tester", admin_password="secret123", web_session_secret="test-secret"
    )
    monkeypatch.setattr("newspipe.web.app.get_settings", lambda: patched)
    monkeypatch.setattr("newspipe.web.auth.get_settings", lambda: patched)
    app = create_app()
    app.testing = True
    return app.test_client()


def test_admin_redirects_to_login_when_unauthenticated(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_news_redirects_to_login_when_unauthenticated(client):
    resp = client.get("/news")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_wrong_credentials_shows_error(client):
    resp = client.post(
        "/login", data={"username": "tester", "password": "wrong"}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


def test_login_success_redirects_to_admin(client):
    resp = client.post("/login", data={"username": "tester", "password": "secret123"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin")


def test_login_then_admin_accessible(client):
    client.post("/login", data={"username": "tester", "password": "secret123"})
    resp = client.get("/admin")
    assert resp.status_code == 200


def test_logout_clears_session(client):
    client.post("/login", data={"username": "tester", "password": "secret123"})
    client.post("/logout")
    resp = client.get("/admin")
    assert resp.status_code == 302


def test_frontpage_has_no_admin_login_link(client):
    resp = client.get("/")
    assert b"Admin Login" not in resp.data
    assert b"/login" not in resp.data


def test_login_path_is_configurable(monkeypatch):
    patched = Settings(
        admin_username="tester",
        admin_password="secret123",
        web_session_secret="test-secret",
        admin_login_path="/portal-test123",
    )
    monkeypatch.setattr("newspipe.web.app.get_settings", lambda: patched)
    monkeypatch.setattr("newspipe.web.auth.get_settings", lambda: patched)
    app = create_app()
    app.testing = True
    client = app.test_client()

    assert client.get("/login").status_code == 404
    assert client.get("/portal-test123").status_code == 200

    resp = client.post(
        "/portal-test123", data={"username": "tester", "password": "secret123"}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin")


def test_login_disabled_when_admin_password_unset(monkeypatch):
    patched = Settings(admin_password=None, web_session_secret="test-secret")
    monkeypatch.setattr("newspipe.web.app.get_settings", lambda: patched)
    monkeypatch.setattr("newspipe.web.auth.get_settings", lambda: patched)
    app = create_app()
    app.testing = True
    client = app.test_client()

    resp = client.post(
        "/login", data={"username": "admin", "password": "anything"}, follow_redirects=True
    )

    assert b"ADMIN_PASSWORD is not set" in resp.data
