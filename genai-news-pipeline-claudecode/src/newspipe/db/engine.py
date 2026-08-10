"""Postgres connection management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from newspipe.config import get_settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a connection with dict rows; commits on clean exit, rolls back on error."""
    settings = get_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn
