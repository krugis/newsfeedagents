"""Tests for LLM labeling (Sub-phase 1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from newspipe.config import Settings, get_settings
from newspipe.db.arrivals import insert_arrivals
from newspipe.db.labels import (
    insert_label,
    select_unlabeled_stories,
    select_unlabeled_story_sources,
)
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem
from newspipe.labeling.labeler import (
    PROMPT_VERSION,
    ProviderConfig,
    _provider_reachable,
    _round_robin_select,
    _select_for_labeling,
    build_prompt,
    label_unlabeled,
    provider_config,
    resolve_labeler_provider,
)
from newspipe.labeling.schema import HeadlineLabel, build_headline_label_model


@pytest.fixture
def fake_api_key(monkeypatch):
    """Let labeler code pass the API-key guard without hitting the network.

    Pins the provider to "deepseek" (with fallback off) so tests don't
    depend on labeler_provider's real default or trigger a reachability
    probe against the local/glm endpoints.
    """
    patched = Settings(
        llm_api_key="test-key", labeler_provider="deepseek", labeler_fallback_provider="none"
    )
    monkeypatch.setattr("newspipe.labeling.labeler.get_settings", lambda: patched)
    return patched


def _make_story(db_conn, source_scope, name: str, items: list[RawItem]) -> list[int]:
    """Insert arrivals through a scoped source and dedup them into stories."""
    sid = source_scope(name)
    insert_arrivals(db_conn, sid, items)
    db_conn.commit()
    run_dedup()
    rows = db_conn.execute(
        """
        SELECT a.external_id, s.story_id
          FROM arrivals a JOIN stories s ON s.story_id = a.story_id
         WHERE a.external_id = ANY(%s)
        """,
        ([it.external_id for it in items],),
    ).fetchall()
    assert len(rows) == len(items)
    return [row["story_id"] for row in rows]


def _label(**overrides) -> HeadlineLabel:
    base = {
        "is_hot": True,
        "importance": 7,
        "category": "model_release",
        "is_genai_ml_relevant": True,
        "rationale": "A major event.",
    }
    base.update(overrides)
    return HeadlineLabel(**base)


# ---- schema -----------------------------------------------------------


def test_headline_label_valid():
    label = HeadlineLabel(
        is_hot=True,
        importance=8,
        category="model_release",
        is_genai_ml_relevant=True,
        rationale="A major release.",
    )
    assert label.importance == 8
    assert label.category == "model_release"


@pytest.mark.parametrize("importance", [0, 11])
def test_headline_label_importance_out_of_range(importance):
    with pytest.raises(ValidationError):
        HeadlineLabel(
            is_hot=True,
            importance=importance,
            category="other",
            is_genai_ml_relevant=True,
            rationale="x",
        )


def test_headline_label_rejects_unknown_category():
    with pytest.raises(ValidationError):
        HeadlineLabel(
            is_hot=True,
            importance=5,
            category="not_a_real_category",
            is_genai_ml_relevant=True,
            rationale="x",
        )


def test_headline_label_requires_all_fields():
    with pytest.raises(ValidationError):
        HeadlineLabel()


def test_build_headline_label_model_custom_categories_and_range():
    Custom = build_headline_label_model(("breaking", "minor"), 1, 5)

    label = Custom(
        is_hot=True, importance=5, category="breaking", is_genai_ml_relevant=True, rationale="x"
    )
    assert label.importance == 5
    assert label.category == "breaking"

    with pytest.raises(ValidationError):
        Custom(
            is_hot=True,
            importance=6,  # out of the custom 1..5 range
            category="breaking",
            is_genai_ml_relevant=True,
            rationale="x",
        )
    with pytest.raises(ValidationError):
        Custom(
            is_hot=True,
            importance=3,
            category="other",  # not in the custom category set
            is_genai_ml_relevant=True,
            rationale="x",
        )


def test_build_headline_label_model_is_cached():
    a = build_headline_label_model(("x", "y"), 1, 10)
    b = build_headline_label_model(("x", "y"), 1, 10)
    assert a is b


# ---- prompt -----------------------------------------------------------


def test_build_prompt_includes_cross_source_signal():
    prompt = build_prompt(
        {
            "title": "OpenAI releases GPT-6",
            "sources": ["TechCrunch", "The Verge"],
            "arrival_count": 2,
            "hn_front_page": True,
        }
    )
    assert "OpenAI releases GPT-6" in prompt
    assert "TechCrunch, The Verge" in prompt
    # template wraps the phrase across lines — normalize whitespace before asserting
    assert "cross-source arrival is an explicit importance signal" in " ".join(
        prompt.lower().split()
    )
    assert "HN_FRONT_PAGE: true" in prompt


# ---- provider selection (local/glm/deepseek, fallback) -------------------


def test_provider_config_local():
    settings = Settings(
        local_llm_api_key="k", local_llm_base_url="https://local.example", local_model_name="m"
    )
    cfg = provider_config(settings, "local")
    assert cfg == ProviderConfig("local", "k", "https://local.example", "m")


def test_provider_config_deepseek():
    settings = Settings(
        llm_api_key="k", llm_base_url="https://ds.example", model_name="deepseek-chat"
    )
    cfg = provider_config(settings, "deepseek")
    assert cfg == ProviderConfig("deepseek", "k", "https://ds.example", "deepseek-chat")


def test_provider_config_glm_disables_thinking_by_default():
    settings = Settings(glm_llm_api_key="k")
    cfg = provider_config(settings, "glm")
    assert cfg.extra_body == {"thinking": {"type": "disabled"}}


def test_provider_config_rejects_unknown_name():
    with pytest.raises(ValueError, match="unknown labeler provider"):
        provider_config(Settings(), "bogus")


def test_resolve_uses_primary_when_reachable(monkeypatch):
    monkeypatch.setattr("newspipe.labeling.labeler._provider_reachable", lambda url: True)
    settings = Settings(labeler_provider="local", local_llm_api_key="k")
    assert resolve_labeler_provider(settings).name == "local"


def test_resolve_falls_back_when_primary_key_missing(monkeypatch):
    # local has no key at all — must fall back without even probing reachability.
    def unexpected(url):
        raise AssertionError("should not probe reachability when the key is unset")

    monkeypatch.setattr("newspipe.labeling.labeler._provider_reachable", unexpected)
    settings = Settings(
        labeler_provider="local",
        local_llm_api_key=None,
        labeler_fallback_provider="glm",
        glm_llm_api_key="gk",
    )
    provider = resolve_labeler_provider(settings)
    assert provider.name == "glm"
    assert provider.api_key == "gk"


def test_resolve_falls_back_when_primary_unreachable(monkeypatch):
    monkeypatch.setattr("newspipe.labeling.labeler._provider_reachable", lambda url: False)
    settings = Settings(
        labeler_provider="local",
        local_llm_api_key="k",
        labeler_fallback_provider="glm",
        glm_llm_api_key="gk",
    )
    assert resolve_labeler_provider(settings).name == "glm"


def test_resolve_stays_on_primary_when_fallback_is_none(monkeypatch):
    monkeypatch.setattr("newspipe.labeling.labeler._provider_reachable", lambda url: False)
    settings = Settings(
        labeler_provider="local", local_llm_api_key="k", labeler_fallback_provider="none"
    )
    provider = resolve_labeler_provider(settings)
    assert provider.name == "local"  # unreachable, but no fallback configured


def test_resolve_stays_on_primary_when_fallback_also_has_no_key(monkeypatch):
    monkeypatch.setattr("newspipe.labeling.labeler._provider_reachable", lambda url: False)
    settings = Settings(
        labeler_provider="local",
        local_llm_api_key="k",
        labeler_fallback_provider="glm",
        glm_llm_api_key=None,
    )
    provider = resolve_labeler_provider(settings)
    assert provider.name == "local"  # fallback has no key either, so stick with primary


def test_provider_reachable_false_on_connection_error(monkeypatch):
    import httpx

    def fake_get(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("newspipe.labeling.labeler.httpx.get", fake_get)
    assert _provider_reachable("https://unreachable.example") is False


def test_provider_reachable_true_on_client_error_response(monkeypatch):
    def fake_get(url, timeout):
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr("newspipe.labeling.labeler.httpx.get", fake_get)
    assert _provider_reachable("https://example.com") is True


def test_provider_reachable_false_on_server_error_response(monkeypatch):
    def fake_get(url, timeout):
        # e.g. a Cloudflare Tunnel error page (530) — the edge answered, but
        # the actual backend behind it is unreachable.
        return httpx.Response(530, request=httpx.Request("GET", url))

    monkeypatch.setattr("newspipe.labeling.labeler.httpx.get", fake_get)
    assert _provider_reachable("https://example.com") is False


# ---- persistence layer -------------------------------------------------


def test_insert_label_roundtrip(db_conn, source_scope, fake_api_key):
    (story_id,) = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-insert",
        [RawItem(external_id="zz-insert-1", url="https://example.com/i", title="Insert Me")],
    )
    label_id = insert_label(
        db_conn,
        story_id,
        is_hot=False,
        importance=4,
        category="industry",
        is_genai_ml_relevant=True,
        rationale="meh",
        model="m",
        prompt_version="p1",
    )
    row = db_conn.execute("SELECT * FROM labels WHERE label_id = %s", (label_id,)).fetchone()
    assert row is not None
    assert row["story_id"] == story_id
    assert row["is_hot"] is False
    assert row["importance"] == 4
    assert row["category"] == "industry"
    assert row["model"] == "m"
    assert row["prompt_version"] == "p1"


def test_select_unlabeled_stories_joins_source_names(db_conn, source_scope):
    sid_a = source_scope("zz-test-label-srca")
    sid_b = source_scope("zz-test-label-srcb")
    insert_arrivals(
        db_conn,
        sid_a,
        [RawItem(external_id="zz-s1", url="https://example.com/x", title="Shared Title One")],
    )
    insert_arrivals(
        db_conn,
        sid_b,
        [RawItem(external_id="zz-s2", url="https://example.com/y", title="Shared Title One")],
    )
    db_conn.commit()
    run_dedup()

    # target the test story explicitly — the dev DB carries thousands of real
    # unlabeled stories, so a plain `limit` would return those, not ours
    row = db_conn.execute(
        """
        SELECT s.* FROM stories s
        JOIN arrivals a ON a.story_id = s.story_id
        WHERE a.external_id = 'zz-s1'
        """
    ).fetchone()
    assert row is not None
    rows = select_unlabeled_stories(db_conn, story_ids=[row["story_id"]])
    assert len(rows) == 1
    match = rows[0]
    assert set(match["sources"]) == {"zz-test-label-srca", "zz-test-label-srcb"}
    assert match["arrival_count"] == 2


def test_select_unlabeled_stories_excludes_labeled(db_conn, source_scope, fake_api_key):
    (story_id,) = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-exclude",
        [RawItem(external_id="zz-ex-1", url="https://example.com/e", title="Already Labeled")],
    )
    insert_label(
        db_conn,
        story_id,
        is_hot=True,
        importance=5,
        category="other",
        is_genai_ml_relevant=True,
        rationale="done",
        model="m",
        prompt_version="p1",
    )
    db_conn.commit()
    rows = select_unlabeled_stories(db_conn, story_ids=[story_id])
    assert rows == []


# ---- batch labeling (model mocked) --------------------------------------


def test_label_unlabeled_persists_labels(db_conn, source_scope, fake_api_key, monkeypatch):
    (story_id,) = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-a",
        [RawItem(external_id="zz-l1", url="https://example.com/l1", title="Label Test Story One")],
    )

    async def fake_batch(stories, provider):  # noqa: ARG001
        return [_label()]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch)
    stats = label_unlabeled(story_ids=[story_id])

    assert stats.stories_attempted == 1
    assert stats.labels_created == 1
    assert stats.failed == 0
    assert stats.stories == [
        {
            "title": "Label Test Story One",
            "is_hot": True,
            "importance": 7,
            "category": "model_release",
        }
    ]

    row = db_conn.execute(
        """
        SELECT l.* FROM labels l
        JOIN stories s ON s.story_id = l.story_id
        WHERE s.title = 'Label Test Story One'
        """
    ).fetchone()
    assert row is not None
    assert row["importance"] == 7
    assert row["category"] == "model_release"
    assert row["prompt_version"] == PROMPT_VERSION
    assert row["model"] == fake_api_key.model_name


def test_label_failure_leaves_story_unlabeled(db_conn, source_scope, fake_api_key, monkeypatch):
    story_ids = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-fail",
        [
            RawItem(external_id="zz-f1", url="https://example.com/f1", title="Good Story"),
            RawItem(external_id="zz-f2", url="https://example.com/f2", title="Bad Story"),
        ],
    )

    async def fake_batch(stories, provider):
        return [_label(), RuntimeError("boom")]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch)
    stats = label_unlabeled(story_ids=story_ids)

    assert stats.labels_created == 1
    assert stats.failed == 1

    # the failed story stays unlabeled and is retried on the next run
    async def fake_batch_ok(stories, provider):
        return [_label(importance=3, category="research") for _ in stories]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch_ok)
    stats2 = label_unlabeled(story_ids=story_ids)
    assert stats2.stories_attempted == 1
    assert stats2.labels_created == 1
    assert stats2.failed == 0


def test_label_unlabeled_story_ids_filter(db_conn, source_scope, fake_api_key, monkeypatch):
    story_ids = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-ids",
        [
            RawItem(external_id="zz-i1", url="https://example.com/i1", title="Id Story One"),
            RawItem(external_id="zz-i2", url="https://example.com/i2", title="Id Story Two"),
            RawItem(external_id="zz-i3", url="https://example.com/i3", title="Id Story Three"),
        ],
    )

    async def fake_batch(stories, provider):
        return [_label() for _ in stories]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch)
    stats = label_unlabeled(story_ids=story_ids[:2])

    assert stats.stories_attempted == 2
    assert stats.labels_created == 2
    assert stats.failed == 0

    unlabeled = select_unlabeled_stories(db_conn, story_ids=[story_ids[2]])
    assert [r["story_id"] for r in unlabeled] == [story_ids[2]]


def test_label_unlabeled_requires_api_key(monkeypatch):
    patched = Settings(
        labeler_provider="local", labeler_fallback_provider="none", local_llm_api_key=None
    )
    monkeypatch.setattr("newspipe.labeling.labeler.get_settings", lambda: patched)
    with pytest.raises(RuntimeError, match="No API key configured"):
        label_unlabeled()


# ---- live (real model, requires ANTHROPIC_API_KEY) -----------------------


@pytest.mark.live
def test_label_three_stories_live(db_conn, source_scope):
    if not resolve_labeler_provider(get_settings()).api_key:
        pytest.skip("no labeler provider has an API key set — set one in .env to run live labeling")
    story_ids = _make_story(
        db_conn,
        source_scope,
        "zz-live-label",
        [
            RawItem(
                external_id="zz-live-1",
                url="https://example.com/live/1",
                title="OpenAI releases a new frontier model",
            ),
            RawItem(
                external_id="zz-live-2",
                url="https://example.com/live/2",
                title="EU publishes draft AI regulation amendments",
            ),
            RawItem(
                external_id="zz-live-3",
                url="https://example.com/live/3",
                title="New research improves diffusion model sampling speed",
            ),
        ],
    )
    stats = label_unlabeled(story_ids=story_ids)
    assert stats.stories_attempted == 3
    assert stats.labels_created == 3
    assert stats.failed == 0
    for row in stats.stories:
        assert row["category"] in {
            "model_release",
            "research",
            "industry",
            "funding",
            "policy_regulation",
            "tooling_infra",
            "other",
        }


# ---- labeling order (round-robin per source, newest first) ---------------


def _row(story_id: int, source_id: int, hours_ago: float) -> dict:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    return {
        "story_id": story_id,
        "source_id": source_id,
        "first_seen_at": now - timedelta(hours=hours_ago),
    }


def test_round_robin_select_fair_across_sources():
    rows = [
        _row(1, source_id=10, hours_ago=1),
        _row(2, source_id=10, hours_ago=2),
        _row(3, source_id=10, hours_ago=3),
        _row(4, source_id=20, hours_ago=0.5),
    ]
    # source 10 has 3 candidates, source 20 has 1 — one from each per round
    # (by source_id order), newest within each source first
    assert _round_robin_select(rows, limit=3) == [1, 4, 2]


def test_round_robin_select_newest_first_within_source():
    rows = [
        _row(1, source_id=10, hours_ago=3),
        _row(2, source_id=10, hours_ago=1),
        _row(3, source_id=10, hours_ago=2),
    ]
    assert _round_robin_select(rows, limit=3) == [2, 3, 1]


def test_round_robin_select_respects_limit():
    rows = [_row(i, source_id=i % 3, hours_ago=i) for i in range(10)]
    assert len(_round_robin_select(rows, limit=4)) == 4


def test_round_robin_select_empty_rows():
    assert _round_robin_select([], limit=10) == []


def test_select_unlabeled_story_sources_earliest_arrival_wins(db_conn, source_scope):
    sid_a = source_scope("zz-rr-srca")
    sid_b = source_scope("zz-rr-srcb")
    insert_arrivals(
        db_conn,
        sid_a,
        [
            RawItem(
                external_id="zz-rr-a1", url="https://example.com/rr-a", title="Zz RR Shared Title"
            )
        ],
    )
    db_conn.commit()
    run_dedup()
    insert_arrivals(
        db_conn,
        sid_b,
        [
            RawItem(
                external_id="zz-rr-b1", url="https://example.com/rr-b", title="Zz RR Shared Title"
            )
        ],
    )
    db_conn.commit()
    run_dedup()

    story = db_conn.execute(
        """
        SELECT s.story_id FROM stories s
        JOIN arrivals a ON a.story_id = s.story_id
        WHERE a.external_id = 'zz-rr-a1'
        """
    ).fetchone()

    rows = select_unlabeled_story_sources(db_conn)
    match = next(r for r in rows if r["story_id"] == story["story_id"])
    assert match["source_id"] == sid_a  # source A's arrival came first


def test_select_for_labeling_uses_round_robin_when_newest_per_source(monkeypatch):
    patched = Settings(llm_api_key="k", label_order="newest_per_source")
    monkeypatch.setattr("newspipe.labeling.labeler.get_settings", lambda: patched)
    monkeypatch.setattr(
        "newspipe.labeling.labeler.select_unlabeled_story_sources",
        lambda conn: [_row(1, 10, 1), _row(2, 20, 1)],
    )
    captured = {}

    def fake_select_unlabeled_stories(conn, limit=None, story_ids=None):
        captured["story_ids"] = story_ids
        return [{"story_id": sid} for sid in story_ids]

    monkeypatch.setattr(
        "newspipe.labeling.labeler.select_unlabeled_stories", fake_select_unlabeled_stories
    )

    result = _select_for_labeling(conn=None, limit=2, story_ids=None)

    assert captured["story_ids"] is not None
    assert len(result) == 2


def test_select_for_labeling_uses_oldest_first_when_configured(monkeypatch):
    patched = Settings(llm_api_key="k", label_order="oldest_first")
    monkeypatch.setattr("newspipe.labeling.labeler.get_settings", lambda: patched)

    def unexpected(conn):
        raise AssertionError("should not be called in oldest_first mode")

    monkeypatch.setattr("newspipe.labeling.labeler.select_unlabeled_story_sources", unexpected)
    captured = {}

    def fake_select_unlabeled_stories(conn, limit=None, story_ids=None):
        captured["limit"] = limit
        captured["story_ids"] = story_ids
        return []

    monkeypatch.setattr(
        "newspipe.labeling.labeler.select_unlabeled_stories", fake_select_unlabeled_stories
    )

    _select_for_labeling(conn=None, limit=5, story_ids=None)

    assert captured["limit"] == 5
    assert captured["story_ids"] is None


def test_select_for_labeling_story_ids_bypasses_strategy(monkeypatch):
    def unexpected(conn):
        raise AssertionError("should not be called when story_ids is given")

    monkeypatch.setattr("newspipe.labeling.labeler.select_unlabeled_story_sources", unexpected)
    captured = {}

    def fake_select_unlabeled_stories(conn, limit=None, story_ids=None):
        captured["story_ids"] = story_ids
        return []

    monkeypatch.setattr(
        "newspipe.labeling.labeler.select_unlabeled_stories", fake_select_unlabeled_stories
    )

    _select_for_labeling(conn=None, limit=100, story_ids=[7, 8])

    assert captured["story_ids"] == [7, 8]
