"""HeadlineLabel schema validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from newspipe.labeling.schema import HeadlineLabel


def _label(**overrides: object) -> HeadlineLabel:
    defaults: dict[str, object] = {
        "is_hot": True,
        "importance": 7,
        "category": "model_release",
        "is_genai_ml_relevant": True,
        "rationale": "A new frontier model shipped.",
    }
    defaults.update(overrides)
    return HeadlineLabel(**defaults)  # type: ignore[arg-type]


def test_valid_label() -> None:
    label = _label()
    assert label.is_hot is True
    assert label.importance == 7
    assert label.category == "model_release"
    assert label.is_genai_ml_relevant is True
    assert label.rationale


@pytest.mark.parametrize("importance", [0, 11, -1])
def test_importance_out_of_range_rejected(importance: int) -> None:
    with pytest.raises(ValidationError):
        _label(importance=importance)


def test_importance_bounds_accepted() -> None:
    assert _label(importance=1).importance == 1
    assert _label(importance=10).importance == 10


@pytest.mark.parametrize(
    "category",
    [
        "model_release",
        "research",
        "industry",
        "funding",
        "policy_regulation",
        "tooling_infra",
        "other",
    ],
)
def test_valid_categories(category: str) -> None:
    assert _label(category=category).category == category  # type: ignore[arg-type]


def test_invalid_category_rejected() -> None:
    with pytest.raises(ValidationError):
        _label(category="sports")


def test_missing_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        HeadlineLabel(is_hot=True, importance=5)  # noqa: E501
