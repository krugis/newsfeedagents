"""Tests for the admin page's editable-config machinery.

Every write test monkeypatches `settings_editor.ENV_FILE` to a tmp_path file
— this suite must never write to the real `.env`, which holds the real LLM
key.
"""

from __future__ import annotations

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from newspipe.config import Settings, get_settings
from newspipe.web import settings_editor


def test_build_candidate_overrides_importance_range():
    candidate = settings_editor.build_candidate({"IMPORTANCE_MIN": "2", "IMPORTANCE_MAX": "8"})
    assert (candidate.importance_min, candidate.importance_max) == (2, 8)


def test_build_candidate_rejects_invalid_importance_range():
    with pytest.raises(ValidationError):
        settings_editor.build_candidate({"IMPORTANCE_MIN": "9", "IMPORTANCE_MAX": "5"})


def test_build_candidate_rejects_non_integer():
    with pytest.raises(ValueError):
        settings_editor.build_candidate({"BATCH_CONCURRENCY": "not-a-number"})


def test_build_candidate_parses_categories_and_retention():
    candidate = settings_editor.build_candidate(
        {"LABEL_CATEGORIES": "a, b ,c", "RETENTION_DAYS": ""}
    )
    assert candidate.label_categories == ("a", "b", "c")
    assert candidate.retention_days is None


def test_build_candidate_blank_llm_api_key_means_unset():
    candidate = settings_editor.build_candidate({"LLM_API_KEY": "  "})
    assert candidate.llm_api_key is None


def test_build_candidate_accepts_valid_label_order():
    candidate = settings_editor.build_candidate({"LABEL_ORDER": "oldest_first"})
    assert candidate.label_order == "oldest_first"


def test_build_candidate_rejects_invalid_label_order():
    with pytest.raises(ValidationError):
        settings_editor.build_candidate({"LABEL_ORDER": "bogus"})


def test_label_order_field_has_both_choices():
    field = next(f for f in settings_editor.EDITABLE_SETTINGS if f.field == "label_order")
    assert field.input_type == "select"
    assert field.choices == ("newest_per_source", "oldest_first")


def test_field_display_value_formats_tuple_and_none():
    settings = Settings(label_categories=("a", "b"), retention_days=None)
    categories_field = next(
        f for f in settings_editor.EDITABLE_SETTINGS if f.field == "label_categories"
    )
    retention_field = next(
        f for f in settings_editor.EDITABLE_SETTINGS if f.field == "retention_days"
    )
    assert settings_editor.field_display_value(settings, categories_field) == "a, b"
    assert settings_editor.field_display_value(settings, retention_field) == ""


def test_save_writes_env_file_without_touching_real_env(tmp_path, monkeypatch):
    tmp_env = tmp_path / ".env"
    monkeypatch.setattr(settings_editor, "ENV_FILE", tmp_env)
    candidate = Settings(
        importance_min=2, importance_max=9, label_categories=("x", "y"), retention_days=None
    )

    settings_editor.save(candidate)

    values = dotenv_values(tmp_env)
    assert values["IMPORTANCE_MIN"] == "2"
    assert values["IMPORTANCE_MAX"] == "9"
    assert values["LABEL_CATEGORIES"] == "x,y"
    assert values["RETENTION_DAYS"] == ""


def test_save_clears_the_settings_cache(tmp_path, monkeypatch):
    tmp_env = tmp_path / ".env"
    monkeypatch.setattr(settings_editor, "ENV_FILE", tmp_env)
    before = get_settings()

    settings_editor.save(get_settings())

    after = get_settings()
    assert before is not after
