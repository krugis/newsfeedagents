"""LLM labeling of stories — Sub-phase 1.3.

Each unlabeled story is sent to the labeling model with its title, the source
names it arrived from, its `arrival_count`, and whether it hit the HN front
page. The model returns a `HeadlineLabel` via structured output, and one
`labels` row is persisted per story per labeling. A story that fails labeling
stays unlabeled and is picked up on the next run — it never blocks the batch.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from newspipe.config import get_settings
from newspipe.db.engine import connect
from newspipe.db.labels import (
    insert_label,
    select_unlabeled_stories,
    select_unlabeled_story_sources,
)
from newspipe.labeling.schema import build_headline_label_model

logger = logging.getLogger(__name__)

PROMPT_VERSION = "p1"

_PROMPT_TEMPLATE = """You label a single GenAI/ML news headline for a daily digest.

TITLE: {title}
SOURCES: {sources}
ARRIVAL_COUNT: {arrival_count}
HN_FRONT_PAGE: {hn_front_page}

Label only the headline above, returning the structured schema you were asked
for. The schema's fields mean:

- is_hot: True only for a major/breaking GenAI/ML event (a model release, a
  significant research result, a major policy decision, a large funding
  round). Routine coverage, speculation, and incremental updates are not hot.
- importance: an integer {importance_min}..{importance_max}. Cross-source
  arrival is an explicit importance signal: a headline carried by several
  independent sources, or that reached the Hacker News front page
  (HN_FRONT_PAGE true), is more likely important than a single-feed mention.
  Weigh that signal without inflating it blindly.
- category: the single best fit.
- is_genai_ml_relevant: False if the headline is NOT about GenAI/ML — generic
  tech/software news or non-AI topics that broad feeds carry. We use this to
  filter non-AI items out.
- rationale: exactly one sentence justifying your is_hot and importance.
"""


@dataclass
class LabelStats:
    """Counters from one labeling run, plus the freshly-labeled rows."""

    stories_attempted: int = 0
    labels_created: int = 0
    failed: int = 0
    stories: list[dict] = field(default_factory=list)  # {title, is_hot, importance, category}
    # story_ids that received a label this run, for run stats
    labeled_story_ids: list[int] = field(default_factory=list)


def build_labeler():
    """A structured-output chat model for labeling, with transient retries."""
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not set — set it in .env to label")
    schema = build_headline_label_model(
        tuple(settings.label_categories), settings.importance_min, settings.importance_max
    )
    # DeepSeek's OpenAI-compatible API does not support json_schema response
    # format, so force function_calling: the schema is carried as a tool the
    # model must call, which enforces field conformance better than json_mode.
    # (Most OpenAI-compatible endpoints accept function_calling too.)
    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    ).with_structured_output(schema, method="function_calling")
    return model.with_retry()


def build_prompt(story: dict) -> str:
    """Render the versioned prompt for one story (see PROMPT_VERSION)."""
    settings = get_settings()
    return _PROMPT_TEMPLATE.format(
        title=story["title"],
        sources=", ".join(story["sources"]),
        arrival_count=story["arrival_count"],
        hn_front_page="true" if story["hn_front_page"] else "false",
        importance_min=settings.importance_min,
        importance_max=settings.importance_max,
    )


async def _batch_label(stories: list[dict]) -> list[BaseModel | Exception]:
    """Run the model over `stories`; one labeled result per story (or its exception)."""
    settings = get_settings()
    labeler = build_labeler()
    prompts = [build_prompt(story) for story in stories]
    return await labeler.abatch(
        prompts,
        config={"max_concurrency": settings.batch_concurrency},
        return_exceptions=True,
    )


def _round_robin_select(rows: list[dict], limit: int) -> list[int]:
    """Fairly interleave unlabeled stories across sources, newest first.

    `rows` are {"story_id", "source_id", "first_seen_at"}, one per unlabeled
    story (see `select_unlabeled_story_sources`). Returns up to `limit`
    story_ids, round-robining across sources one story per round — so no
    single source can fill the whole budget — newest within each source
    first. A source with fewer stories than its "fair share" simply runs dry
    early, and its unused rounds go to the other sources automatically.
    """
    by_source: dict[int, deque[int]] = {}
    for row in sorted(rows, key=lambda r: r["first_seen_at"], reverse=True):
        by_source.setdefault(row["source_id"], deque()).append(row["story_id"])

    queues = [by_source[source_id] for source_id in sorted(by_source)]
    selected: list[int] = []
    while queues and len(selected) < limit:
        for queue in list(queues):
            if len(selected) == limit:
                break
            selected.append(queue.popleft())
            if not queue:
                queues.remove(queue)
    return selected


def _select_for_labeling(conn, limit: int | None, story_ids: list[int] | None) -> list[dict]:
    """Pick which unlabeled stories to label this run."""
    if story_ids is not None:
        return select_unlabeled_stories(conn, story_ids=story_ids)
    settings = get_settings()
    if limit is not None and settings.label_order == "newest_per_source":
        candidates = select_unlabeled_story_sources(conn)
        selected_ids = _round_robin_select(candidates, limit)
        return select_unlabeled_stories(conn, story_ids=selected_ids) if selected_ids else []
    return select_unlabeled_stories(conn, limit=limit)


def label_unlabeled(limit: int | None = None, story_ids: list[int] | None = None) -> LabelStats:
    """Label unlabeled stories and persist one `labels` row each.

    Which stories are picked is governed by `Settings.label_order` (see
    `_select_for_labeling`); explicit `story_ids` always wins (used by tests
    and targeted relabeling). Failures are counted and left unlabeled so a
    later run retries them; a failed story never blocks the rest of the batch.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not set — set it in .env to label")
    with connect() as conn:
        stories = _select_for_labeling(conn, limit, story_ids)
        stats = LabelStats(stories_attempted=len(stories))
        if not stories:
            return stats
        outcomes = asyncio.run(_batch_label(stories))
        for story, outcome in zip(stories, outcomes, strict=True):
            if isinstance(outcome, Exception):
                stats.failed += 1
                logger.warning("labeling failed for story %s: %s", story["story_id"], outcome)
                continue
            insert_label(
                conn,
                story["story_id"],
                is_hot=outcome.is_hot,
                importance=outcome.importance,
                category=outcome.category,
                is_genai_ml_relevant=outcome.is_genai_ml_relevant,
                rationale=outcome.rationale,
                model=settings.model_name,
                prompt_version=PROMPT_VERSION,
            )
            stats.labels_created += 1
            stats.labeled_story_ids.append(story["story_id"])
            stats.stories.append(
                {
                    "title": story["title"],
                    "is_hot": outcome.is_hot,
                    "importance": outcome.importance,
                    "category": outcome.category,
                }
            )
    return stats


def main(limit: int | None = None) -> int:
    """CLI entry: label unlabeled stories and print a table of the new labels."""
    settings = get_settings()
    limit = limit if limit is not None else settings.label_limit_per_run
    try:
        stats = label_unlabeled(limit=limit)
    except RuntimeError as exc:  # e.g. missing LLM_API_KEY — a config error
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"stories attempted: {stats.stories_attempted}")
    print(f"labels created:    {stats.labels_created}")
    if stats.failed:
        print(f"failed (left unlabeled, retried next run): {stats.failed}")
    print()
    print("title\tis_hot\timportance\tcategory")
    for s in stats.stories:
        print(f"{s['title']}\t{s['is_hot']}\t{s['importance']}\t{s['category']}")
    return 0
