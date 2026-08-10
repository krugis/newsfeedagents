"""Structured label schema produced by the labeling LLM."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "model_release",
    "research",
    "industry",
    "funding",
    "policy_regulation",
    "tooling_infra",
    "other",
]


class HeadlineLabel(BaseModel):
    """One labeling of a story."""

    is_hot: bool  # major/breaking GenAI-ML event vs routine
    importance: int = Field(ge=1, le=10)  # 1-10
    category: Category
    is_genai_ml_relevant: bool  # The Verge/GNews bring non-AI items; filter here
    rationale: str  # one sentence
