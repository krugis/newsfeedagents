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
    base = {
        "label_id": 1,
        "story_id": 1,
        "is_hot": True,
        "category": "model_release",
        "labeled_at": _now(),
    }
    Label(importance=1, **base)
    Label(importance=10, **base)
    with pytest.raises(ValidationError):
        Label(importance=0, **base)
    with pytest.raises(ValidationError):
        Label(importance=11, **base)


def test_label_category_is_literal() -> None:
    base = {
        "label_id": 1,
        "story_id": 1,
        "is_hot": False,
        "importance": 5,
        "labeled_at": _now(),
    }
    with pytest.raises(ValidationError):
        Label(category="not_a_category", **base)
