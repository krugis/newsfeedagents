"""Phase 1 source registry seed data + idempotent seeding."""

from __future__ import annotations

import psycopg

from newspipe.db.sources import upsert_source

SOURCES: list[dict] = [
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
        # Anthropic publishes no public RSS feed (verified at build time); its
        # sitemap lists every /news/ article, fetched by the sitemap fetcher.
        "name": "Anthropic Blog",
        "method": "sitemap",
        "config": {
            "sitemap_url": "https://www.anthropic.com/sitemap.xml",
            "path_filter": "/news/",
        },
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
            "base_url": "https://hn.algolia.com/api/v1/",
            "keywords": ["AI", "LLM", "generative AI", "machine learning"],
            "hits_per_page": 100,
        },
    },
    {
        "name": "Google News: generative AI",
        "method": "google_news_rss",
        "config": {
            "feed_url": "https://news.google.com/rss/search?q=generative%20AI&hl=en-US&gl=US&ceid=US:en",
        },
    },
    {
        "name": "Google News: large language model",
        "method": "google_news_rss",
        "config": {
            "feed_url": "https://news.google.com/rss/search?q=large%20language%20model&hl=en-US&gl=US&ceid=US:en",
        },
    },
]


def seed(conn: psycopg.Connection) -> int:
    """Upsert all Phase 1 sources; returns how many sources were upserted."""
    for source in SOURCES:
        upsert_source(conn, source["name"], source["method"], source["config"])
    return len(SOURCES)
