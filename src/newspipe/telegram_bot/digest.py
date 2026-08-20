"""Pure helpers for the Telegram bot: lookback-window parsing + HTML formatting.

Kept free of any aiogram/network dependency so they're trivial to unit test.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta

_HOURS_RE = re.compile(r"(\d+)\s*h(?:ours?)?", re.IGNORECASE)
_DAY_WORDS = ("today", "daily", "day")


def parse_window(text: str, *, default_hours: int) -> tuple[timedelta, str]:
    """Parse free text into a lookback window + a human-readable label.

    Recognizes "<N>h"/"<N> hours" and "today"/"daily"/"day" (since UTC
    midnight); anything else (including empty text) falls back to
    `default_hours`.
    """
    normalized = text.strip().lower()
    if normalized and any(word in normalized for word in _DAY_WORDS):
        now = datetime.now(UTC)
        midnight = datetime(now.year, now.month, now.day, tzinfo=UTC)
        return now - midnight, "today"
    match = _HOURS_RE.search(normalized) if normalized else None
    if match:
        hours = int(match.group(1))
        return timedelta(hours=hours), f"last {hours}h"
    return timedelta(hours=default_hours), f"last {default_hours}h"


def format_digest(stories: list[dict], window_label: str) -> str:
    """Render stories (as returned by `db.stories.select_top_stories`) as HTML.

    Uses Telegram's HTML parse mode rather than MarkdownV2 — titles come from
    external feeds and may contain markdown-special characters that would
    otherwise break parsing; HTML only needs `html.escape` to stay safe.
    """
    if not stories:
        return f"No GenAI/ML news in the {html.escape(window_label)}."
    lines = [f"<b>GenAI/ML News — {html.escape(window_label)}</b>", ""]
    for i, story in enumerate(stories, start=1):
        title = html.escape(story["title"])
        url = html.escape(story["canonical_url"] or "")
        sources = html.escape(", ".join(story["sources"]))
        hot = " · HOT" if story["is_hot"] else ""
        lines.append(f'{i}. <a href="{url}">{title}</a>')
        lines.append(f"   <i>{sources}</i> · importance {story['importance']}{hot}")
    return "\n".join(lines)
