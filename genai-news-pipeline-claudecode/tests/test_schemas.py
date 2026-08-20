"""Validation tests for the Pydantic domain models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from newspipe.models.schemas import Label, Source


def _now() -> datetime:
    return datetime.now(UTC)


def test_source_method_must_be_known() -> None:
    with pytest.raises(ValidationError):
        Source(source_id=1, name="bad", method="unknown", created_at=_now())


def test_label_importance_bounds() -> None:
    """The persisted-row model allows 1..100 — a ceiling covering any

    configured importance scale (Settings.importance_min/max); the exact
    configured bounds are enforced at the LLM structured-output boundary.
    """
    base = {
        "label_id": 1,
        "story_id": 1,
        "is_hot": True,
        "category": "model_release",
        "labeled_at": _now(),
    }
    Label(importance=1, **base)
    Label(importance=100, **base)
    with pytest.raises(ValidationError):
        Label(importance=0, **base)
    with pytest.raises(ValidationError):
        Label(importance=101, **base)


def test_label_category_accepts_any_string() -> None:
    """`Label.category` is a plain str — the category enum is configurable

    (Settings.label_categories) and enforced at the LLM structured-output
    boundary (labeling/schema.py), not on this persisted-row model.
    """
    base = {
        "label_id": 1,
        "story_id": 1,
        "is_hot": False,
        "importance": 5,
        "labeled_at": _now(),
    }
    assert Label(category="any_configured_category", **base).category == "any_configured_category"
