"""Seed the Phase 1 source registry (idempotent upsert).

Run with: ``uv run python scripts/seed_sources.py``
"""

from __future__ import annotations

import json

from sqlalchemy import text

from newspipe.db.engine import get_engine

SOURCES = [
    {
        "name": "TechCrunch AI",
        "method": "rss",
        "config": {"feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    },
    {
        "name": "VentureBeat AI",
        "method": "rss",
        "config": {"feed_url": "https://venturebeat.com/category/ai/feed/"},
    },
    {
        "name": "The Verge",
        "method": "rss",
        "config": {"feed_url": "https://www.theverge.com/rss/index.xml"},
    },
    {
        "name": "Anthropic Blog",
        "method": "rss",
        "config": {
            "note": (
                "Anthropic publishes no official RSS feed (verified at build, "
                "2026-08-09). Disabled pending a chosen feed (e.g. a community "
                "mirror) or a Phase 2 scraper."
            )
        },
        "enabled": False,
    },
    {
        "name": "OpenAI Blog",
        "method": "rss",
        "config": {"feed_url": "https://openai.com/news/rss.xml"},
    },
    {
        "name": "Hacker News",
        "method": "hn_algolia",
        "config": {
            "api_base": "https://hn.algolia.com/api/v1/",
            "keywords": ["AI", "LLM", "GenAI", "artificial intelligence"],
            "front_page": True,
        },
    },
    {
        "name": "Google News: generative AI",
        "method": "google_news_rss",
        "config": {"query": "generative AI"},
    },
    {
        "name": "Google News: large language model",
        "method": "google_news_rss",
        "config": {"query": "large language model"},
    },
]

_UPSERT = text(
    """
    INSERT INTO sources (name, method, config, enabled)
    VALUES (:name, :method, CAST(:config AS jsonb), :enabled)
    ON CONFLICT (name) DO UPDATE
        SET method = EXCLUDED.method,
            config = EXCLUDED.config,
            enabled = EXCLUDED.enabled
    """
)


def seed() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for src in SOURCES:
            conn.execute(
                _UPSERT,
                {
                    "name": src["name"],
                    "method": src["method"],
                    "config": json.dumps(src["config"]),
                    "enabled": src.get("enabled", True),
                },
            )
    print(f"seeded {len(SOURCES)} sources (idempotent)")


if __name__ == "__main__":
    seed()
