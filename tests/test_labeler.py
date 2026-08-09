"""Labeler tests with the model call mocked."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import bindparam, text

from newspipe.db.engine import get_engine
from newspipe.labeling.labeler import (
    PROMPT_VERSION,
    StoryContext,
    _label_batch,
    build_chain,
    build_messages,
    persist_labels,
    run_label,
    select_unlabeled_stories,
)
from newspipe.labeling.schema import HeadlineLabel


class _FakeSettings:
    def __init__(self, api_key: str, model: str, concurrency: int = 2) -> None:
        self.anthropic_api_key = api_key
        self.anthropic_model = model
        self.batch_concurrency = concurrency


class FakeChain:
    """Minimal Runnable stand-in returning canned labels."""

    def __init__(self, labels: list[HeadlineLabel], fail_on: set[str] | None = None) -> None:
        self._labels = labels
        self._fail_on = fail_on or set()

    async def abatch(self, inputs: list, config: dict | None = None) -> list[HeadlineLabel]:
        if config is None or "max_concurrency" not in config:
            raise AssertionError("abatch must be called with max_concurrency config")
        # fail the whole batch if any input contains a failure marker
        for inp in inputs:
            body = " ".join(m.content for m in inp)
            for marker in self._fail_on:
                if marker in body:
                    raise RuntimeError("batch failure")
        return [self._labels[0]] * len(inputs)

    async def ainvoke(self, inp) -> HeadlineLabel:
        body = " ".join(m.content for m in inp)
        for marker in self._fail_on:
            if marker in body:
                raise RuntimeError("item failure")
        return self._labels[0]


def _story(**overrides: object) -> StoryContext:
    defaults: dict[str, object] = {
        "story_id": 1,
        "title": "OpenAI releases GPT-6",
        "source_names": ["OpenAI Blog", "TechCrunch AI"],
        "arrival_count": 3,
        "hn_front_page": True,
    }
    defaults.update(overrides)
    return StoryContext(**defaults)  # type: ignore[arg-type]


def _label(**overrides: object) -> HeadlineLabel:
    defaults: dict[str, object] = {
        "is_hot": True,
        "importance": 8,
        "category": "model_release",
        "is_genai_ml_relevant": True,
        "rationale": "New flagship model.",
    }
    defaults.update(overrides)
    return HeadlineLabel(**defaults)  # type: ignore[arg-type]


def test_build_messages_includes_context() -> None:
    prompt_vars = build_messages(_story())
    body = str(prompt_vars)
    assert "OpenAI releases GPT-6" in body
    assert "OpenAI Blog, TechCrunch AI" in body
    assert "3" in body
    assert "yes" in body


def test_build_messages_hn_no() -> None:
    prompt_vars = build_messages(_story(hn_front_page=False, source_names=[]))
    body = str(prompt_vars)
    assert "no" in body
    assert "(none)" in body


def test_build_chain_uses_structured_output_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, list] = {"init": [], "structured": [], "retry": []}

    class FakeStructured:
        def with_retry(self) -> object:
            calls["retry"].append(True)
            return object()

    class FakeModel:
        def with_structured_output(self, schema) -> FakeStructured:
            calls["structured"].append(schema)
            return FakeStructured()

    def fake_init_chat_model(
        model_name: str, model_provider: str, api_key: str | None
    ) -> FakeModel:
        calls["init"].append((model_name, model_provider))
        return FakeModel()

    monkeypatch.setattr("newspipe.labeling.labeler.init_chat_model", fake_init_chat_model)
    build_chain(api_key="test-key")
    assert calls["init"] == [("claude-sonnet-4-6", "anthropic")]
    assert calls["structured"] == [HeadlineLabel]
    assert calls["retry"] == [True]


def test_run_label_skips_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "newspipe.labeling.labeler.get_settings",
        lambda: _FakeSettings(api_key=None, model="m", concurrency=2),
    )
    stats = run_label()
    assert stats == {"skipped": True}


def _insert_story(conn, canonical_url: str, title: str) -> int:
    row = conn.execute(
        text(
            "INSERT INTO stories (canonical_url, title, title_hash, first_seen_at, last_seen_at)"
            " VALUES (:c, :t, :h, now(), now()) RETURNING story_id"
        ),
        {"c": canonical_url, "t": title, "h": "h-" + canonical_url},
    ).fetchone()
    return int(row[0])


def test_select_and_persist_labels_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = get_engine()
    story_ids: list[int] = []
    with engine.begin() as conn:
        story_ids.append(_insert_story(conn, "https://dedup.test/lbl/1", "Label me one"))
        story_ids.append(_insert_story(conn, "https://dedup.test/lbl/2", "Label me two"))

    try:
        # 1. selection: both test stories come back unlabeled
        with engine.connect() as conn:
            contexts = select_unlabeled_stories(conn)
        test_contexts = [c for c in contexts if c.story_id in story_ids]
        assert len(test_contexts) == 2

        # 2. labeling via the batch function with a fake chain
        label = _label()
        fake = FakeChain([label])
        monkeypatch.setattr(
            "newspipe.labeling.labeler.get_settings",
            lambda: _FakeSettings(api_key="x", model="claude-sonnet-4-6", concurrency=2),
        )
        results = asyncio.run(_label_batch(fake, test_contexts))
        assert set(results) == set(story_ids)

        # 3. persistence
        with engine.begin() as conn:
            persist_labels(conn, results, "claude-sonnet-4-6")

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT story_id, is_hot, importance, category, is_genai_ml_relevant,"
                    " rationale, model, prompt_version FROM labels WHERE story_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": tuple(story_ids)},
            ).mappings().fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row["is_hot"] is True
            assert row["importance"] == 8
            assert row["category"] == "model_release"
            assert row["is_genai_ml_relevant"] is True
            assert row["model"] == "claude-sonnet-4-6"
            assert row["prompt_version"] == PROMPT_VERSION

        # 4. labeled stories are no longer selected
        with engine.connect() as conn:
            remaining = select_unlabeled_stories(conn)
        assert all(s.story_id not in story_ids for s in remaining)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM labels WHERE story_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": tuple(story_ids)},
            )
            conn.execute(
                text("DELETE FROM stories WHERE story_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": tuple(story_ids)},
            )


def test_batch_failure_isolates_items(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = get_engine()
    story_ids: list[int] = []
    with engine.begin() as conn:
        story_ids.append(_insert_story(conn, "https://dedup.test/lbl/good", "Good story"))
        story_ids.append(_insert_story(conn, "https://dedup.test/lbl/bad", "Bad story marker"))

    try:
        with engine.connect() as conn:
            contexts = select_unlabeled_stories(conn)
        test_contexts = [c for c in contexts if c.story_id in story_ids]
        assert len(test_contexts) == 2

        label = _label()
        fake = FakeChain([label], fail_on={"Bad story marker"})
        monkeypatch.setattr(
            "newspipe.labeling.labeler.get_settings",
            lambda: _FakeSettings(api_key="x", model="m", concurrency=1),
        )
        results = asyncio.run(_label_batch(fake, test_contexts))

        assert story_ids[0] in results
        assert story_ids[1] not in results

        with engine.begin() as conn:
            persist_labels(conn, results, "m")

        # the failed story stays unlabeled -> still selected
        with engine.connect() as conn:
            remaining = select_unlabeled_stories(conn)
        assert story_ids[1] in {s.story_id for s in remaining}
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM labels WHERE story_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": tuple(story_ids)},
            )
            conn.execute(
                text("DELETE FROM stories WHERE story_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": tuple(story_ids)},
            )
