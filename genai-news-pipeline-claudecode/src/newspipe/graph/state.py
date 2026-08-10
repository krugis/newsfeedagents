"""Checkpointed state flowing through the pipeline graph (Sub-phase 1.4)."""

from __future__ import annotations

from datetime import datetime
from operator import add
from typing import Annotated, Any, TypedDict


class PipelineState(TypedDict, total=False):
    """State flowing through the graph, checkpointed by the PostgresSaver.

    `fetch_results` and `errors` need the `add` reducer because the parallel
    fetch_source invocations each append their own slice. The other list
    fields get the same reducer so a resumed run replays them exactly (a
    reducer makes the channel append-only regardless of write order).
    """

    thread_id: str
    run_id: int | None
    due_source_ids: list[int]
    fetch_results: Annotated[list[dict[str, Any]], add]
    new_arrival_ids: Annotated[list[int], add]
    affected_story_ids: Annotated[list[int], add]
    labeled_story_ids: Annotated[list[int], add]
    errors: Annotated[list[str], add]
    dedup_stats: dict[str, Any]
    stats: dict[str, Any]
    # transient: carried in each Send payload to a fetch_source invocation,
    # never written back into the parent state
    source_id: int
    started_at: datetime | None
