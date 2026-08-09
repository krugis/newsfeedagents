"""Database engine factory (SQLAlchemy Core + psycopg3)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from newspipe.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.sqlalchemy_url, pool_pre_ping=True, future=True)
