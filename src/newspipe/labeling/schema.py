"""The structured-output contract for headline labeling.

The labeling model returns exactly this shape, and it is persisted one-for-one
into `labels` (see `db/labels.py`). Kept out of the db layer so persistence
stays decoupled from the LLM boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from newspipe.models.schemas import Category


class HeadlineLabel(BaseModel):
    """One label for one headline, produced by the labeling model."""

    is_hot: bool = Field(description="major/breaking GenAI-ML event vs routine")
    importance: int = Field(ge=1, le=10, description="1 (minor) .. 10 (breaking)")
    category: Category
    is_genai_ml_relevant: bool = Field(
        description="True if the headline is about GenAI/ML — broad feeds carry non-AI items"
    )
    rationale: str = Field(description="one sentence justifying is_hot and importance")
