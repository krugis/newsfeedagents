"""Editable-configuration machinery for the admin page.

Every pipeline setting except `database_url` (changing the DB connection via
a form served *by* a connection to that same DB is a footgun — a bad value
locks the admin out with no easy recovery) can be viewed and edited here.
Submitted values round-trip through the real `Settings` model, so every
existing validator (importance bounds, non-empty categories, positive
retention_days, ...) applies before anything is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import set_key

from newspipe.config import Settings, get_settings

# A module-level constant (not a literal inside the functions below) so tests
# can point writes at a tmp_path file instead of the real .env, which holds
# the real LLM key.
ENV_FILE = Path(".env")

InputType = Literal["text", "password", "number", "optional_number"]


@dataclass(frozen=True)
class SettingField:
    env_var: str
    field: str
    input_type: InputType
    label: str
    help_text: str = ""


EDITABLE_SETTINGS: tuple[SettingField, ...] = (
    SettingField(
        "LLM_API_KEY",
        "llm_api_key",
        "password",
        "LLM API key",
        "Any OpenAI-compatible endpoint's key. Leave blank to disable labeling.",
    ),
    SettingField("LLM_BASE_URL", "llm_base_url", "text", "LLM base URL"),
    SettingField("MODEL_NAME", "model_name", "text", "Model name"),
    SettingField(
        "BATCH_CONCURRENCY",
        "batch_concurrency",
        "number",
        "Batch concurrency",
        "Max concurrent LLM calls when labeling a batch.",
    ),
    SettingField(
        "LABEL_LIMIT_PER_RUN",
        "label_limit_per_run",
        "number",
        "Label limit per run",
        "Cap on unlabeled stories labeled per run (backfill guard).",
    ),
    SettingField(
        "LABEL_INTERVAL_MINUTES",
        "label_interval_minutes",
        "number",
        "Label interval (minutes)",
        "0 = label on every pipeline run, independent of fetch cadence otherwise.",
    ),
    SettingField(
        "LABEL_CATEGORIES", "label_categories", "text", "Label categories", "Comma-separated."
    ),
    SettingField("IMPORTANCE_MIN", "importance_min", "number", "Importance min"),
    SettingField("IMPORTANCE_MAX", "importance_max", "number", "Importance max"),
    SettingField("SCHEDULER_CRON_MINUTE", "scheduler_cron_minute", "text", "Scheduler cron minute"),
    SettingField("SCHEDULER_CRON_HOUR", "scheduler_cron_hour", "text", "Scheduler cron hour"),
    SettingField(
        "RETENTION_DAYS",
        "retention_days",
        "optional_number",
        "Retention (days)",
        "Blank = keep news forever.",
    ),
)


def field_display_value(settings: Settings, field: SettingField) -> str:
    """Render a field's current value for the form (comma-join tuples, blank for None)."""
    value = getattr(settings, field.field)
    if value is None:
        return ""
    if isinstance(value, tuple):
        return ", ".join(value)
    return str(value)


def _coerce(field: SettingField, raw: str) -> object:
    raw = raw.strip()
    if field.input_type == "number":
        return int(raw)
    if field.input_type == "optional_number":
        return int(raw) if raw else None
    if field.field == "llm_api_key":
        return raw or None
    return raw


def build_candidate(form: dict[str, str]) -> Settings:
    """Validate submitted form values against every existing Settings validator.

    Raises ValueError/pydantic.ValidationError (nothing written yet at this
    point) if the resulting settings would be invalid, or if a numeric field
    doesn't parse as an integer.
    """
    current = get_settings().model_dump()
    for field in EDITABLE_SETTINGS:
        if field.env_var in form:
            current[field.field] = _coerce(field, form[field.env_var])
    return Settings(**current)


def save(settings: Settings) -> None:
    """Persist every editable field to ENV_FILE and drop the settings cache."""
    ENV_FILE.touch(exist_ok=True)
    for field in EDITABLE_SETTINGS:
        value = getattr(settings, field.field)
        if value is None:
            rendered = ""
        elif isinstance(value, tuple):
            rendered = ",".join(value)
        else:
            rendered = str(value)
        set_key(str(ENV_FILE), field.env_var, rendered)
    get_settings.cache_clear()
