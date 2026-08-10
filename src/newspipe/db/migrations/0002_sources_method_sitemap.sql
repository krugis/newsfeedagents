-- 0002_sources_method_sitemap: Anthropic publishes no public RSS feed, so the
-- sources method CHECK constraint gains 'sitemap' (an XML sitemap fetcher).
ALTER TABLE sources DROP CONSTRAINT sources_method_check;
ALTER TABLE sources ADD CONSTRAINT sources_method_check
    CHECK (method IN ('rss', 'hn_algolia', 'google_news_rss', 'sitemap'));
