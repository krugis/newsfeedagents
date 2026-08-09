"""LangGraph shared state for the news pipeline.

``fetch_results``, ``new_arrival_ids``, and ``errors`` use ``operator.add``
reducers so the parallel fetch fan-out accumulates them safely.
"""

from __future__ import annotations

from typing import Annotated, TypedDict, operator


class PipelineState(TypedDict, total=False):
    run_id: str
    due_source_ids: list[int]
    fetch_results: Annotated[list[dict], operator.add]
    new_arrival_ids: Annotated[list[int], operator.add]
    affected_story_ids: list[int]
    labeled_story_ids: list[int]
    errors: Annotated[list[str], operator.add]
    stats: dict


INITIAL_STATE: PipelineState = {
    "due_source_ids": [],
    "fetch_results": [],
    "new_arrival_ids": [],
    "affected_story_ids": [],
    "labeled_story_ids": [],
    "errors": [],
    "stats": {},
}
