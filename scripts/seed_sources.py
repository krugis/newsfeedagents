#!/usr/bin/env python
"""Seed the source registry with the Phase 1 sources (idempotent).

Usage: uv run python scripts/seed_sources.py
"""

from __future__ import annotations

from newspipe.db.engine import connect
from newspipe.seeding import seed

if __name__ == "__main__":
    with connect() as conn:
        count = seed(conn)
    print(f"Seeded {count} sources (idempotent upsert).")
