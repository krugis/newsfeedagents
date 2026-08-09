"""LLM labeling: batch labeling of unlabeled stories.

The chain is ``init_chat_model(...).with_structured_output(HeadlineLabel)``
wrapped in ``.with_retry()``. The provider/model are env-driven
(``DEEPSEEK_MODEL`` via langchain-deepseek). Prompt is a versioned constant
stored into ``labels.prompt_version``. Cross-source arrival is an explicit
importance signal in the prompt. A story that fails labeling stays unlabeled
(picked up next run) and never blocks the rest of the batch.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel
from sqlalchemy import text

from newspipe.config import get_settings
from newspipe.db.engine import get_engine
from newspipe.labeling.schema import HeadlineLabel

PROMPT_VERSION = "p1"

PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You are an expert news editor curating a GenAI/ML intelligence feed. "
                "For the headline below, decide whether it is a major/breaking GenAI-ML "
                "event versus routine news, rate its importance from 1 (trivial) to "
                "10 (industry-defining), choose the single best category, and say "
                "whether it is relevant to GenAI/ML at all (some sources carry general "
                "tech news that should be filtered out). "
                "IMPORTANT: if an item arrived from multiple distinct sources, that "
                "cross-source corroboration is a strong signal of importance and should "
                "be reflected in your importance score."
            ),
        ),
        (
            "human",
            (
                "Title: {title}\n"
                "Arrived from sources: {sources}\n"
                "Number of source arrivals: {arrival_count}\n"
                "On Hacker News front page: {hn_front_page}"
            ),
        ),
    ]
)


class StoryContext(BaseModel):
    """Everything the prompt needs about one unlabeled story."""

    story_id: int
    title: str
    source_names: list[str]
    arrival_count: int
    hn_front_page: bool


def build_chain(api_key: str | None = None) -> Runnable:
    """Build the structured-output labeling chain with retries."""
    settings = get_settings()
    key = api_key if api_key is not None else settings.deepseek_api_key
    model: BaseChatModel = init_chat_model(
        settings.deepseek_model, model_provider="deepseek", api_key=key
    )
    return model.with_structured_output(HeadlineLabel).with_retry()


def build_messages(story: StoryContext) -> dict[str, Any]:
    return {
        "title": story.title,
        "sources": ", ".join(story.source_names) or "(none)",
        "arrival_count": story.arrival_count,
        "hn_front_page": "yes" if story.hn_front_page else "no",
    }


async def _label_batch(chain: Runnable, contexts: list[StoryContext]) -> dict[int, HeadlineLabel]:
    """Label a batch; per-item failures are dropped, never raised."""
    settings = get_settings()
    results: dict[int, HeadlineLabel] = {}
    for chunk in _chunks(contexts, settings.batch_concurrency):
        inputs = [PROMPT.format_messages(**build_messages(ctx)) for ctx in chunk]
        try:
            labels = await chain.abatch(
                inputs, config={"max_concurrency": settings.batch_concurrency}
            )
            for ctx, label in zip(chunk, labels, strict=False):
                results[ctx.story_id] = label
        except Exception:  # noqa: BLE001 - retry each item individually
            for ctx in chunk:
                try:
                    label = await chain.ainvoke(PROMPT.format_messages(**build_messages(ctx)))
                    results[ctx.story_id] = label
                except Exception:  # noqa: BLE001 - story stays unlabeled for next run
                    continue
    return results


def _chunks(items: list[StoryContext], size: int) -> list[list[StoryContext]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def persist_labels(conn, labels: dict[int, HeadlineLabel], model: str) -> int:
    """Insert one labels row per labeled story; returns row count."""
    count = 0
    for story_id, label in labels.items():
        conn.execute(
            text(
                "INSERT INTO labels (story_id, is_hot, importance, category,"
                " is_genai_ml_relevant, rationale, model, prompt_version) VALUES"
                " (:story_id, :is_hot, :importance, :category, :relevant,"
                "  :rationale, :model, :prompt_version)"
            ),
            {
                "story_id": story_id,
                "is_hot": label.is_hot,
                "importance": label.importance,
                "category": label.category,
                "relevant": label.is_genai_ml_relevant,
                "rationale": label.rationale,
                "model": model,
                "prompt_version": PROMPT_VERSION,
            },
        )
        count += 1
    return count


def select_unlabeled_stories(conn, limit: int | None = None) -> list[StoryContext]:
    stmt = (
        "SELECT st.story_id, st.title, st.arrival_count, st.hn_front_page,"
        " COALESCE(array_agg(DISTINCT s.name) FILTER (WHERE s.name IS NOT NULL),"
        "          '{}') AS source_names"
        " FROM stories st"
        " LEFT JOIN arrivals a ON a.story_id = st.story_id"
        " LEFT JOIN sources s ON s.source_id = a.source_id"
        " WHERE NOT EXISTS (SELECT 1 FROM labels l WHERE l.story_id = st.story_id)"
        " GROUP BY st.story_id"
        " ORDER BY st.story_id"
    )
    if limit is not None:
        stmt += " LIMIT :limit"
    params = {"limit": limit} if limit is not None else {}
    rows = conn.execute(text(stmt), params).mappings().fetchall()
    return [StoryContext(**dict(row)) for row in rows]


def run_label(limit: int | None = None, chain: Runnable | None = None) -> dict:
    """Label up to ``limit`` unlabeled stories end-to-end (sync wrapper)."""
    settings = get_settings()
    if not settings.deepseek_api_key:
        return {"skipped": True}
    used_chain = chain or build_chain()
    engine = get_engine()
    with engine.begin() as conn:
        contexts = select_unlabeled_stories(conn, limit=limit)
    results = asyncio.run(_label_batch(used_chain, contexts))
    labeled = 0
    with engine.begin() as conn:
        labeled = persist_labels(conn, results, settings.deepseek_model)
    return {
        "skipped": False,
        "selected": len(contexts),
        "labeled": labeled,
        "failed": len(contexts) - len(results),
        "results": results,
        "contexts": contexts,
    }
