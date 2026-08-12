"""The structured-output contract for headline labeling.

The labeling model returns exactly this shape, and it is persisted one-for-one
into `labels` (see `db/labels.py`). Kept out of the db layer so persistence
stays decoupled from the LLM boundary.

The category set and importance scale are configurable
(`Settings.label_categories`/`importance_min`/`importance_max`), so the
schema class is built dynamically per those bounds — `HeadlineLabel` below is
just the default-settings instance, kept for backward-compatible direct use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from newspipe.config import DEFAULT_CATEGORIES


@lru_cache
def build_headline_label_model(
    categories: tuple[str, ...], importance_min: int, importance_max: int
) -> type[BaseModel]:
    """Build a `HeadlineLabel`-shaped model constrained to the given bounds.

    `category` becomes a `Literal` over `categories` so the LLM's structured
    output (function-calling schema) enforces the configured enum, and
    `importance` is bounded to `[importance_min, importance_max]`. Cached so
    repeated calls with the same settings return the same class.
    """
    return create_model(
        "HeadlineLabel",
        is_hot=(bool, Field(description="major/breaking GenAI-ML event vs routine")),
        importance=(
            int,
            Field(
                ge=importance_min,
                le=importance_max,
                description=f"{importance_min} (minor) .. {importance_max} (breaking)",
            ),
        ),
        category=(Literal[categories], ...),
        is_genai_ml_relevant=(
            bool,
            Field(
                description=(
                    "True if the headline is about GenAI/ML — broad feeds carry non-AI items"
                )
            ),
        ),
        rationale=(str, Field(description="one sentence justifying is_hot and importance")),
    )


HeadlineLabel = build_headline_label_model(DEFAULT_CATEGORIES, 1, 10)
