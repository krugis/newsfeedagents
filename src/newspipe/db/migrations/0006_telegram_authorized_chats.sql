-- 0006_telegram_authorized_chats: self-service access via a shared join
-- code (see telegram_bot/bot.py's /join command) — a chat that redeems the
-- correct TELEGRAM_ACCESS_CODE gets a row here and is allowed to use the
-- bot from then on, alongside the static TELEGRAM_ALLOWED_CHAT_IDS list.

CREATE TABLE telegram_authorized_chats (
    chat_id        BIGINT PRIMARY KEY,
    chat_type      TEXT NOT NULL,
    authorized_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
