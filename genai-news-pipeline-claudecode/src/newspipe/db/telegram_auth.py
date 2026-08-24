"""Self-service Telegram access (the `telegram_authorized_chats` table).

A chat that redeems the correct join code (see `telegram_bot/bot.py`'s
`/join` command) gets a row here and stays authorized across restarts,
alongside the static `Settings.telegram_allowed_chat_ids` list.
"""

from __future__ import annotations

import psycopg


def is_authorized(conn: psycopg.Connection, chat_id: int) -> bool:
    """True if `chat_id` has previously redeemed the join code."""
    row = conn.execute(
        "SELECT 1 FROM telegram_authorized_chats WHERE chat_id = %s", (chat_id,)
    ).fetchone()
    return row is not None


def authorize(conn: psycopg.Connection, chat_id: int, chat_type: str) -> None:
    """Record `chat_id` as authorized (idempotent — redeeming twice is a no-op)."""
    conn.execute(
        """
        INSERT INTO telegram_authorized_chats (chat_id, chat_type)
        VALUES (%s, %s)
        ON CONFLICT (chat_id) DO NOTHING
        """,
        (chat_id, chat_type),
    )
