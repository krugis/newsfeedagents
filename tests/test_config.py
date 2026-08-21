"""Tests for configurable settings (Settings validators/parsing)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from newspipe.config import Settings


def test_label_categories_default():
    settings = Settings()
    assert settings.label_categories == (
        "model_release",
        "research",
        "industry",
        "funding",
        "policy_regulation",
        "tooling_infra",
        "other",
    )


def test_label_categories_parses_comma_separated_string():
    settings = Settings(label_categories="a, b ,c")
    assert settings.label_categories == ("a", "b", "c")


def test_label_categories_rejects_empty():
    with pytest.raises(ValidationError):
        Settings(label_categories="   ")


@pytest.mark.parametrize(
    "importance_min,importance_max",
    [(5, 5), (10, 1), (0, 10), (1, 101)],
)
def test_importance_range_rejects_invalid_bounds(importance_min, importance_max):
    with pytest.raises(ValidationError):
        Settings(importance_min=importance_min, importance_max=importance_max)


def test_importance_range_accepts_custom_bounds():
    settings = Settings(importance_min=1, importance_max=5)
    assert (settings.importance_min, settings.importance_max) == (1, 5)


def test_retention_days_defaults_unset():
    assert Settings().retention_days is None


def test_retention_days_rejects_non_positive():
    with pytest.raises(ValidationError):
        Settings(retention_days=0)


def test_scheduler_cron_defaults_reproduce_original_schedule():
    settings = Settings()
    assert settings.scheduler_cron_minute == "5"
    assert settings.scheduler_cron_hour == "*"


def test_label_interval_minutes_default_is_every_run():
    assert Settings().label_interval_minutes == 0


def test_label_order_default_is_newest_per_source():
    assert Settings().label_order == "newest_per_source"


def test_label_order_rejects_invalid_value():
    with pytest.raises(ValidationError):
        Settings(label_order="not_a_real_order")


def test_admin_login_path_default():
    assert Settings().admin_login_path == "/login"


def test_admin_login_path_accepts_custom_value():
    assert Settings(admin_login_path="/portal-abc123").admin_login_path == "/portal-abc123"


@pytest.mark.parametrize(
    "value",
    ["login", "//evil.com", "/", "/topic", "/admin", "/news", "/logout", "/admin/settings"],
)
def test_admin_login_path_rejects_invalid_or_reserved(value):
    with pytest.raises(ValidationError):
        Settings(admin_login_path=value)
