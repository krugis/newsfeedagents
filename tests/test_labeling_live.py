"""Live labeling test: labels 3 real stories with the real model chain.

Skipped cleanly when ANTHROPIC_API_KEY is unset.
"""

from __future__ import annotations

import asyncio

import pytest

from newspipe.config import get_settings
from newspipe.labeling.labeler import StoryContext, _label_batch, build_chain
from newspipe.labeling.schema import HeadlineLabel

LIVE_STORIES = [
    StoryContext(
        story_id=0,
        title="Anthropic and OpenAI both ship new frontier reasoning models this week",
        source_names=["TechCrunch AI", "VentureBeat AI", "Hacker News"],
        arrival_count=4,
        hn_front_page=True,
    ),
    StoryContext(
        story_id=0,
        title="Startup raises $200M series B for enterprise AI agents",
        source_names=["VentureBeat AI"],
        arrival_count=1,
        hn_front_page=False,
    ),
    StoryContext(
        story_id=0,
        title="City council debates zoning rules for new downtown stadium",
        source_names=["The Verge"],
        arrival_count=1,
        hn_front_page=False,
    ),
]


@pytest.mark.live
def test_live_label_three_stories() -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    chain = build_chain(api_key=settings.anthropic_api_key)
    results = asyncio.run(_label_batch(chain, LIVE_STORIES))

    assert len(results) == 3
    labels = list(results.values())
    for label in labels:
        assert isinstance(label, HeadlineLabel)
        assert 1 <= label.importance <= 10
        assert label.rationale

    # the third story is a non-AI city council item -> must be marked non-relevant
    non_ai = labels[2]
    assert non_ai.is_genai_ml_relevant is False
