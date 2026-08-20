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
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from newspipe.config import get_settings
from newspipe.db.engine import connect
from newspipe.db.stories import select_top_stories
from newspipe.logging_setup import setup_logging
from newspipe.telegram_bot.digest import format_digest, parse_window

logger = logging.getLogger(__name__)

router = Router()

_HELP_TEXT = (
    "Ask me for GenAI/ML news: /news, /news 6h, /news today — "
    "or @-mention me the same way in a group."
)


def build_digest_text(window_text: str) -> str:
    """Query the DB and render a digest for the given free-text window arg."""
    settings = get_settings()
    window, label = parse_window(window_text, default_hours=settings.telegram_default_window_hours)
    end = datetime.now(UTC)
    start = end - window
    with connect() as conn:
        stories = select_top_stories(conn, start, end, limit=settings.telegram_digest_limit)
    return format_digest(stories, label)


@router.message(Command("news", "digest"))
async def cmd_news(message: Message, command: CommandObject) -> None:
    text = build_digest_text(command.args or "")
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(_HELP_TEXT)


@router.message()
async def on_mention(message: Message, bot: Bot) -> None:
    """Catches whatever privacy mode still lets through that isn't a command
    handled above: an @mention in a group, or a reply to this bot."""
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
