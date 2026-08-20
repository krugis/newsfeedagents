"""Tests for the Telegram bot's pure logic: window parsing, digest formatting,
and the DB-to-text pipeline. Deliberately excludes aiogram/network wiring —
those have no meaningful logic of their own to test beyond what aiogram's
own test suite already covers.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from newspipe.config import Settings
from newspipe.db import telegram_auth
from newspipe.db.arrivals import insert_arrivals
from newspipe.db.labels import insert_label
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem
from newspipe.telegram_bot.bot import build_digest_text, is_allowed
from newspipe.telegram_bot.digest import format_digest, parse_window

# ---- db.telegram_auth --------------------------------------------------


@pytest.fixture
def tg_chat_id(db_conn):
    """A distinctive fake chat id, cleaned up from telegram_authorized_chats after."""
    chat_id = -9999000111
    yield chat_id
    db_conn.execute("DELETE FROM telegram_authorized_chats WHERE chat_id = %s", (chat_id,))
    db_conn.commit()


def test_authorize_then_is_authorized(db_conn, tg_chat_id):
    assert telegram_auth.is_authorized(db_conn, tg_chat_id) is False
    telegram_auth.authorize(db_conn, tg_chat_id, "group")
    db_conn.commit()
    assert telegram_auth.is_authorized(db_conn, tg_chat_id) is True


def test_authorize_is_idempotent(db_conn, tg_chat_id):
    telegram_auth.authorize(db_conn, tg_chat_id, "group")
    telegram_auth.authorize(db_conn, tg_chat_id, "group")  # must not raise (ON CONFLICT DO NOTHING)
    db_conn.commit()
    assert telegram_auth.is_authorized(db_conn, tg_chat_id) is True


# ---- is_allowed (access control) -------------------------------------------


def test_is_allowed_open_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(
        "newspipe.telegram_bot.bot.get_settings",
        lambda: Settings(telegram_allowed_chat_ids=(), telegram_access_code=None),
    )
    assert is_allowed(12345) is True
    assert is_allowed(-100999) is True


def test_is_allowed_restricts_to_the_static_list(monkeypatch):
    monkeypatch.setattr(
        "newspipe.telegram_bot.bot.get_settings",
        lambda: Settings(telegram_allowed_chat_ids=(111, -222), telegram_access_code=None),
    )
    assert is_allowed(111) is True
    assert is_allowed(-222) is True
    assert is_allowed(333) is False


def test_is_allowed_true_for_db_authorized_chat_when_code_configured(
    monkeypatch, db_conn, tg_chat_id
):
    monkeypatch.setattr(
        "newspipe.telegram_bot.bot.get_settings",
        lambda: Settings(telegram_allowed_chat_ids=(), telegram_access_code="secret123"),
    )
    assert is_allowed(tg_chat_id) is False  # not yet authorized
    telegram_auth.authorize(db_conn, tg_chat_id, "private")
    db_conn.commit()
    assert is_allowed(tg_chat_id) is True


# ---- parse_window ---------------------------------------------------------


def test_parse_window_hours():
    window, label = parse_window("6h", default_hours=3)
    assert window == timedelta(hours=6)
    assert label == "last 6h"


def test_parse_window_hours_with_word():
    window, label = parse_window("give me 12 hours please", default_hours=3)
    assert window == timedelta(hours=12)
    assert label == "last 12h"


def test_parse_window_today():
    window, label = parse_window("today", default_hours=3)
    assert label == "today"
    assert timedelta() <= window <= timedelta(hours=24)


def test_parse_window_daily_word_variant():
    window, label = parse_window("daily digest please", default_hours=3)
    assert label == "today"


def test_parse_window_empty_falls_back_to_default():
    window, label = parse_window("", default_hours=5)
    assert window == timedelta(hours=5)
    assert label == "last 5h"


def test_parse_window_unrecognized_falls_back_to_default():
    window, label = parse_window("what's new", default_hours=4)
    assert window == timedelta(hours=4)
    assert label == "last 4h"


# ---- format_digest ----------------------------------------------------------


def test_format_digest_empty():
    assert format_digest([], "last 6h") == "No GenAI/ML news in the last 6h."


def test_format_digest_escapes_html_and_includes_fields():
    stories = [
        {
            "title": "<script>alert(1)</script> & friends",
            "canonical_url": "https://example.com/x?a=1&b=2",
            "sources": ["Hacker News", "The Verge"],
            "is_hot": True,
            "importance": 8,
        }
    ]
    text = format_digest(stories, "last 3h")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text
    assert "Hacker News, The Verge" in text
    assert "importance 8" in text
    assert "HOT" in text


def test_format_digest_numbers_multiple_stories():
    stories = [
        {
            "title": f"Story {i}",
            "canonical_url": f"https://example.com/{i}",
            "sources": ["Src"],
            "is_hot": False,
            "importance": 5,
        }
        for i in range(3)
    ]
    text = format_digest(stories, "last 3h")
    assert "1. " in text
    assert "2. " in text
    assert "3. " in text


# ---- build_digest_text (real DB, no network) --------------------------------


def test_build_digest_text_includes_recent_labeled_story(monkeypatch, db_conn, source_scope):
    monkeypatch.setattr(
        "newspipe.telegram_bot.bot.get_settings",
        lambda: Settings(telegram_default_window_hours=3, telegram_digest_limit=10),
    )
    sid = source_scope("zz-tg-src")
    insert_arrivals(
        db_conn,
        sid,
        [RawItem(external_id="zz-tg-1", url="https://example.com/tg1", title="Zz Telegram Story")],
    )
    db_conn.commit()
    run_dedup()
    row = db_conn.execute(
        "SELECT s.story_id FROM stories s JOIN arrivals a ON a.story_id = s.story_id "
        "WHERE a.external_id = 'zz-tg-1'"
    ).fetchone()
    insert_label(
        db_conn,
        row["story_id"],
        is_hot=True,
        importance=9,
        category="model_release",
        is_genai_ml_relevant=True,
        rationale="test",
        model="m",
        prompt_version="p1",
    )
    db_conn.commit()

    text = build_digest_text("6h")

    assert "Zz Telegram Story" in text
    assert "importance 9" in text
    assert "HOT" in text


def test_build_digest_text_excludes_non_genai_relevant(monkeypatch, db_conn, source_scope):
    monkeypatch.setattr(
        "newspipe.telegram_bot.bot.get_settings",
        lambda: Settings(telegram_default_window_hours=3, telegram_digest_limit=10),
    )
    sid = source_scope("zz-tg-src2")
    insert_arrivals(
        db_conn,
        sid,
        [
            RawItem(
                external_id="zz-tg-2", url="https://example.com/tg2", title="Zz Irrelevant Story"
            )
        ],
    )
    db_conn.commit()
    run_dedup()
    row = db_conn.execute(
        "SELECT s.story_id FROM stories s JOIN arrivals a ON a.story_id = s.story_id "
        "WHERE a.external_id = 'zz-tg-2'"
    ).fetchone()
    insert_label(
        db_conn,
        row["story_id"],
        is_hot=False,
        importance=3,
        category="other",
        is_genai_ml_relevant=False,
        rationale="not ai",
        model="m",
        prompt_version="p1",
    )
    db_conn.commit()

    text = build_digest_text("6h")

    assert "Zz Irrelevant Story" not in text
