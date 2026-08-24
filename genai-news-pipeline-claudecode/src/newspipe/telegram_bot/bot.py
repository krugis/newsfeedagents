"""aiogram wiring: command/mention handlers + a scheduled daily digest push.

Telegram's default bot "privacy mode" already filters what a group forwards
to us down to: slash commands, @mentions of the bot, and replies to the
bot's own messages — so unlike the WhatsApp design this was built alongside,
there's no manual mention-detection needed for the common cases (commands
are matched by aiogram's `Command` filter below); the catch-all handler only
has to disambiguate real @mentions from a private-chat message.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from newspipe.config import get_settings
from newspipe.db import telegram_auth
from newspipe.db.engine import connect
from newspipe.db.pipeline_runs import select_last_successful_run_finished_at
from newspipe.db.stories import select_stories_by_topic, select_top_stories
from newspipe.logging_setup import setup_logging
from newspipe.telegram_bot.digest import (
    format_digest,
    format_topic_results,
    parse_topic_args,
    parse_window,
)

logger = logging.getLogger(__name__)

router = Router()

_TOPIC_USAGE = "Usage: /topic <keyword> [days] — e.g. /topic gemini 7"

_HELP_TEXT = (
    "Ask me for GenAI/ML news: /news, /news 6h, /news today — "
    "or @-mention me the same way in a group. "
    "Search a topic: /topic gemini (last 3 days) or /topic gemini 7 (up to 7 days)."
)


def build_digest_text(window_text: str) -> str:
    """Query the DB and render a digest for the given free-text window arg."""
    settings = get_settings()
    window, label = parse_window(window_text, default_hours=settings.telegram_default_window_hours)
    end = datetime.now(UTC)
    start = end - window
    with connect() as conn:
        stories = select_top_stories(conn, start, end, limit=settings.telegram_digest_limit)
        last_updated = select_last_successful_run_finished_at(conn)
    return format_digest(stories, label, last_updated)


def build_topic_text(args_text: str) -> str:
    """Query the DB and render topic-search results for the given free-text args.

    `args_text` is the raw `/topic` command args: `<query> [days]`, days
    optional and defaulting/clamped per `topic_search_default_days`/
    `topic_search_max_days` (see `digest.parse_topic_args`).
    """
    settings = get_settings()
    query, days = parse_topic_args(
        args_text,
        default_days=settings.topic_search_default_days,
        max_days=settings.topic_search_max_days,
    )
    if not query:
        return _TOPIC_USAGE
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    with connect() as conn:
        stories = select_stories_by_topic(
            conn, query, start, end, limit=settings.topic_search_limit
        )
        last_updated = select_last_successful_run_finished_at(conn)
    return format_topic_results(stories, query, days, last_updated)


def is_allowed(chat_id: int) -> bool:
    """True if `chat_id` may use the bot.

    Fully open (True for everyone) only when neither restriction mechanism
    is configured — the default. Once either `TELEGRAM_ALLOWED_CHAT_IDS` or
    `TELEGRAM_ACCESS_CODE` is set, access requires being in the static list
    or having redeemed the join code (see `cmd_join`, persisted in
    `telegram_authorized_chats`).
    """
    settings = get_settings()
    if not settings.telegram_allowed_chat_ids and not settings.telegram_access_code:
        return True
    if chat_id in settings.telegram_allowed_chat_ids:
        return True
    with connect() as conn:
        return telegram_auth.is_authorized(conn, chat_id)


async def _reject_if_not_allowed(message: Message) -> bool:
    """Log-and-swallow a message from a chat outside the allow-list.

    No reply is sent — a stranger who finds/adds the bot gets no signal it's
    even listening. The log line doubles as how you discover a chat's id to
    add to the allow-list (send it a message, then check the logs).
    """
    if is_allowed(message.chat.id):
        return False
    # INFO (not DEBUG) on purpose: this is how you discover a chat's id to
    # add to TELEGRAM_ALLOWED_CHAT_IDS, and setup_logging()'s default level
    # is INFO — a DEBUG line here would be invisible during normal setup.
    logger.info(
        "chat_not_allowed",
        extra={"extra_chat_id": message.chat.id, "extra_chat_type": message.chat.type},
    )
    return True


@router.message(Command("news", "digest"))
async def cmd_news(message: Message, command: CommandObject) -> None:
    if await _reject_if_not_allowed(message):
        return
    text = build_digest_text(command.args or "")
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("topic"))
async def cmd_topic(message: Message, command: CommandObject) -> None:
    if await _reject_if_not_allowed(message):
        return
    if not (command.args or "").strip():
        await message.answer(_TOPIC_USAGE)
        return
    text = build_topic_text(command.args)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    if await _reject_if_not_allowed(message):
        return
    await message.answer(_HELP_TEXT)


@router.message(Command("join"))
async def cmd_join(message: Message, command: CommandObject) -> None:
    """Self-service authorization: `/join <code>` — deliberately NOT gated by
    `_reject_if_not_allowed`, since an unauthorized chat is exactly who needs
    to reach this."""
    settings = get_settings()
    if not settings.telegram_access_code:
        await message.answer("This bot isn't using invite codes.")
        return
    if (command.args or "").strip() != settings.telegram_access_code:
        await message.answer("That code didn't work.")
        return
    with connect() as conn:
        telegram_auth.authorize(conn, message.chat.id, message.chat.type)
    await message.answer("You're in — try /news.")


@router.message()
async def on_mention(message: Message, bot: Bot) -> None:
    """Catches whatever privacy mode still lets through that isn't a command
    handled above: an @mention in a group, or a reply to this bot."""
    if await _reject_if_not_allowed(message):
        return
    text = message.text or ""
    if message.chat.type == "private":
        reply_text = build_digest_text(text)
        await message.answer(reply_text, parse_mode="HTML", disable_web_page_preview=True)
        return
    me = await bot.get_me()
    mention = f"@{me.username}"
    if mention not in text:
        return
    stripped = text.replace(mention, "").strip()
    reply_text = build_digest_text(stripped)
    await message.answer(reply_text, parse_mode="HTML", disable_web_page_preview=True)


async def _push_daily_digest(bot: Bot) -> None:
    settings = get_settings()
    text = build_digest_text("today")
    for chat_id in settings.telegram_digest_chat_ids:
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            logger.exception("daily_digest_push_failed", extra={"extra_chat_id": chat_id})


async def _run() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set — set it in .env to run the bot")
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    if settings.telegram_digest_chat_ids:
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _push_daily_digest,
            CronTrigger(
                hour=settings.telegram_daily_digest_cron_hour,
                minute=settings.telegram_daily_digest_cron_minute,
            ),
            args=[bot],
            id="telegram-daily-digest",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )
        scheduler.start()

    logger.info(
        "telegram_bot_started",
        extra={"extra_digest_chats": len(settings.telegram_digest_chat_ids)},
    )
    await dp.start_polling(bot)


def main() -> int:
    setup_logging(to_stdout=True)
    try:
        asyncio.run(_run())
    except RuntimeError as exc:  # e.g. missing TELEGRAM_BOT_TOKEN — a config error
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
