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
    {
        "name": "AI/TLDR",
        "method": "rss",
        "config": {"feed_url": "https://ai-tldr.dev/feed.xml"},
    },
    {
        "name": "Google AI Blog",
        "method": "rss",
        "config": {"feed_url": "https://blog.google/technology/ai/rss/"},
    },
    {
        "name": "Google DeepMind Blog",
        "method": "rss",
        "config": {"feed_url": "https://deepmind.google/blog/feed/basic/"},
    },
    {
        # Qwen (Alibaba) publishes no public RSS feed (verified at build time);
        # its per-locale sitemap lists every /blog/ post, fetched by the
        # sitemap fetcher (the top-level sitemap.xml is a sitemap *index*,
        # which that fetcher doesn't follow, so the en-locale leaf is used).
        "name": "Qwen Blog",
        "method": "sitemap",
        "config": {
            "sitemap_url": "https://qwenlm.github.io/en/sitemap.xml",
            "path_filter": "/blog/",
        },
    },
    {
        # DeepSeek publishes no public RSS feed (verified at build time); its
        # API docs sitemap lists every /news/ entry, fetched by the sitemap
        # fetcher.
        "name": "DeepSeek News",
        "method": "sitemap",
        "config": {
            "sitemap_url": "https://api-docs.deepseek.com/sitemap.xml",
            "path_filter": "/news/",
        },
    },
    {
        # Mistral AI publishes no public RSS feed (verified at build time);
        # its sitemap lists every /news/ entry, fetched by the sitemap
        # fetcher (the top-level sitemap.xml is a sitemap *index*, which that
        # fetcher doesn't follow, so the leaf sitemap-0.xml is used).
        "name": "Mistral AI News",
        "method": "sitemap",
        "config": {
            "sitemap_url": "https://mistral.ai/sitemap-0.xml",
            "path_filter": "/news/",
        },
    },
    {
        # Meta publishes no discoverable public RSS feed or sitemap for
        # ai.meta.com (verified at build time); tracked via Google News search
        # instead, matching the existing generic-topic sources below.
        "name": "Google News: Meta AI Llama",
        "method": "google_news_rss",
        "config": {
            "feed_url": "https://news.google.com/rss/search?q=%22Meta%20AI%22%20OR%20Llama&hl=en-US&gl=US&ceid=US:en",
        },
    },
    {
        "name": "Hugging Face Blog",
        "method": "rss",
        "config": {"feed_url": "https://huggingface.co/blog/feed.xml"},
    },
    {
        "name": "NVIDIA Blog",
        "method": "rss",
        "config": {"feed_url": "https://blogs.nvidia.com/feed/"},
    },
    {
        "name": "MIT Technology Review: AI",
        "method": "rss",
        "config": {
            "feed_url": "https://www.technologyreview.com/topic/artificial-intelligence/feed"
        },
    },
    {
        "name": "Ars Technica: AI",
        "method": "rss",
        "config": {"feed_url": "https://arstechnica.com/ai/feed/"},
    },
    {
        "name": "Wired: AI",
        "method": "rss",
        "config": {"feed_url": "https://www.wired.com/feed/tag/ai/latest/rss"},
    },
    {
        "name": "MarkTechPost",
        "method": "rss",
        "config": {"feed_url": "https://www.marktechpost.com/feed/"},
    },
    {
        "name": "Simon Willison's Blog",
        "method": "rss",
        "config": {"feed_url": "https://simonwillison.net/atom/everything/"},
    },
]


def seed(conn: psycopg.Connection) -> int:
    """Upsert all Phase 1 sources; returns how many sources were upserted."""
    for source in SOURCES:
        upsert_source(conn, source["name"], source["method"], source["config"])
    return len(SOURCES)
