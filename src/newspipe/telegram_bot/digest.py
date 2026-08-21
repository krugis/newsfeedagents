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


def parse_topic_args(text: str, *, default_days: int, max_days: int) -> tuple[str, int]:
    """Parse `/topic <query> [days]` args.

    The last whitespace-separated token, if a bare non-negative integer, is
    the days window (clamped to `[1, max_days]`); everything before it is
    the search query. `/topic gemini` -> ("gemini", default_days);
    `/topic gemini 7` -> ("gemini", 7); `/topic foo bar 40` -> ("foo bar", max_days).
    """
    parts = text.strip().split()
    days = default_days
    if parts and parts[-1].isdigit():
        days = min(max(int(parts[-1]), 1), max_days)
        parts = parts[:-1]
    return " ".join(parts).strip(), days


def format_topic_results(stories: list[dict], query: str, days: int) -> str:
    """Render topic-search results (as returned by `db.stories.select_stories_by_topic`) as HTML.

    Unlike `format_digest`, a story may be unlabeled (`importance is None`)
    since topic search isn't restricted to labeled stories.
    """
    window_label = f"last {days} day{'s' if days != 1 else ''}"
    header = f"<b>Topic: {html.escape(query)}</b> — {html.escape(window_label)}"
    if not stories:
        return f"{header}\nNo news found."
    lines = [header, ""]
    for i, story in enumerate(stories, start=1):
        title = html.escape(story["title"])
        url = html.escape(story["canonical_url"] or "")
        sources = html.escape(", ".join(story["sources"]))
        lines.append(f'{i}. <a href="{url}">{title}</a>')
        if story["importance"] is None:
            lines.append(f"   <i>{sources}</i> · unlabeled")
        else:
            hot = " · HOT" if story["is_hot"] else ""
            lines.append(f"   <i>{sources}</i> · importance {story['importance']}{hot}")
    return "\n".join(lines)


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
