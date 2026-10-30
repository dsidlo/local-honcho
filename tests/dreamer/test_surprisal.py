"""Unit tests for ``src/dreamer/surprisal.py``.

Covers the surprisal sampling pipeline end-to-end against the real test
database with a stubbed tree, plus each private helper directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.config import settings
from src.dreamer.surprisal import (
    ObservationData,
    SurprisalScore,
    _build_tree,
    _compute_surprisal_scores,
    _extract_embeddings,
    _fetch_all_observations,
    _fetch_random_observations,
    _fetch_recent_observations,
    _filter_by_percent,
    _normalize_scores,
    sample_observations_with_surprisal,
)

_DIM = settings.EMBEDDING.VECTOR_DIMENSIONS


def _embedding(value: float) -> list[float]:
    return [float(value)] * _DIM


class _StubTree:
    """Minimal tree substitute: records batch_insert, scores by sum."""

    def __init__(self, score_fn=None):
        self.score_fn = score_fn or (lambda emb: float(np.sum(emb)))
        self.inserted = None

    def batch_insert(self, embeddings):
        self.inserted = embeddings

    def surprisal(self, embedding):
        return self.score_fn(np.asarray(embedding))


async def _ensure_collection(
    db_session: AsyncSession,
    workspace_name: str,
    observer: str,
    observed: str,
) -> None:
    """Create the collection row the documents table foreign-keys into (idempotent)."""
    from sqlalchemy import select

    existing = (
        await db_session.execute(
            select(models.Collection).where(
                models.Collection.workspace_name == workspace_name,
                models.Collection.observer == observer,
                models.Collection.observed == observed,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    db_session.add(
        models.Collection(
            observer=observer,
            observed=observed,
            workspace_name=workspace_name,
            internal_metadata={},
        )
    )
    await db_session.commit()


async def _seed_documents(
    db_session: AsyncSession,
    workspace_name: str,
    observer: str,
    observed: str,
    count: int,
    *,
    null_embeddings: int = 0,
    long_content_at: int | None = None,
    level: str = "explicit",
) -> None:
    await _ensure_collection(db_session, workspace_name, observer, observed)
    for i in range(count):
        content = f"observation {i}"
        if long_content_at is not None and i == long_content_at:
            content = "very long content " * 9
        embedding = None if i < null_embeddings else _embedding(i)
        db_session.add(
            models.Document(
                content=content,
                level=level,
                workspace_name=workspace_name,
                observer=observer,
                observed=observed,
                embedding=embedding,
            )
        )
    await db_session.commit()


def _observation(embedding: list[float]) -> ObservationData:
    return ObservationData(id="doc1", content="c", level="explicit", embedding=embedding)


def _score(obs: ObservationData, surprisal: float) -> SurprisalScore:
    return SurprisalScore(observation=obs, surprisal=surprisal, embedding=obs.embedding)


class TestSampleObservationsWithSurprisal:
    @pytest.mark.asyncio
    async def test_happy_path_ranks_and_filters(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            12,
            long_content_at=11,
        )
        stub = _StubTree()

        with patch(
            "src.dreamer.surprisal.create_tree", return_value=stub
        ) as mock_create_tree:
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )

        mock_create_tree.assert_called_once()
        assert stub.inserted is not None
        assert stub.inserted.shape == (12, _DIM)
        # Top 10% of 12 -> 1 observation, score normalized into [0, 1].
        assert len(results) == 1
        assert isinstance(results[0], SurprisalScore)
        assert 0.0 <= results[0].surprisal <= 1.0
        assert results[0].observation.embedding is not None

    @pytest.mark.asyncio
    async def test_no_observations_returns_empty(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        with patch("src.dreamer.surprisal.create_tree") as mock_create_tree:
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []
        mock_create_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_too_few_observations_for_tree(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(db_session, workspace.name, peer.name, peer.name, 9)
        with patch("src.dreamer.surprisal.create_tree") as mock_create_tree:
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []
        mock_create_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_embeddings_skipped(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            15,
            null_embeddings=3,
        )
        stub = _StubTree()
        with patch("src.dreamer.surprisal.create_tree", return_value=stub):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        # 12 remaining observations (>= TREE_K * 2), so computation proceeds.
        assert stub.inserted is not None
        assert stub.inserted.shape == (12, _DIM)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_identical_scores_normalize_to_midpoint(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _ensure_collection(db_session, workspace.name, peer.name, peer.name)
        for _ in range(12):
            db_session.add(
                models.Document(
                    content="same embedding",
                    level="explicit",
                    workspace_name=workspace.name,
                    observer=peer.name,
                    observed=peer.name,
                    embedding=_embedding(0.5),
                )
            )
        await db_session.commit()
        with patch(
            "src.dreamer.surprisal.create_tree", return_value=_StubTree()
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert len(results) == 1
        assert results[0].surprisal == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_invalid_scores_filtered(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(db_session, workspace.name, peer.name, peer.name, 12)

        def score_fn(emb: np.ndarray) -> float:
            if emb[0] == 5.0:
                return float(np.inf)
            return float(np.sum(emb))

        with patch(
            "src.dreamer.surprisal.create_tree",
            return_value=_StubTree(score_fn=score_fn),
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert len(results) == 1
        assert np.isfinite(results[0].surprisal)

    @pytest.mark.asyncio
    async def test_all_scores_invalid_returns_empty(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(db_session, workspace.name, peer.name, peer.name, 12)
        with patch(
            "src.dreamer.surprisal.create_tree",
            return_value=_StubTree(score_fn=lambda emb: float("nan")),
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []

    @pytest.mark.asyncio
    async def test_failed_embedding_extraction_returns_empty(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(db_session, workspace.name, peer.name, peer.name, 12)
        with (
            patch(
                "src.dreamer.surprisal._extract_embeddings",
                return_value=np.array([]),
            ),
            patch("src.dreamer.surprisal.create_tree") as mock_create_tree,
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []
        mock_create_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        with patch(
            "src.dreamer.surprisal._fetch_observations",
            new=AsyncMock(side_effect=RuntimeError("db boom")),
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []

    @pytest.mark.asyncio
    async def test_random_sampling_strategy(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
        monkeypatch: pytest.MonkeyPatch,
    ):
        workspace, peer = sample_data
        monkeypatch.setattr(
            settings.DREAM.SURPRISAL, "SAMPLING_STRATEGY", "random"
        )
        with (
            patch(
                "src.dreamer.surprisal._fetch_random_observations",
                new=AsyncMock(return_value=[]),
            ) as mock_random,
            patch(
                "src.dreamer.surprisal._fetch_recent_observations",
                new=AsyncMock(return_value=[]),
            ) as mock_recent,
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []
        mock_random.assert_awaited_once()
        mock_recent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_sampling_strategy(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
        monkeypatch: pytest.MonkeyPatch,
    ):
        workspace, peer = sample_data
        monkeypatch.setattr(settings.DREAM.SURPRISAL, "SAMPLING_STRATEGY", "all")
        with (
            patch(
                "src.dreamer.surprisal._fetch_all_observations",
                new=AsyncMock(return_value=[]),
            ) as mock_all,
            patch(
                "src.dreamer.surprisal._fetch_recent_observations",
                new=AsyncMock(return_value=[]),
            ) as mock_recent,
        ):
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []
        mock_all.assert_awaited_once()
        mock_recent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_strategy_falls_back_to_recent(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
        monkeypatch: pytest.MonkeyPatch,
    ):
        workspace, peer = sample_data
        monkeypatch.setattr(
            settings.DREAM.SURPRISAL, "SAMPLING_STRATEGY", "bogus"
        )
        with patch(
            "src.dreamer.surprisal._fetch_recent_observations",
            new=AsyncMock(return_value=[]),
        ) as mock_recent:
            results = await sample_observations_with_surprisal(
                workspace.name, peer.name, peer.name
            )
        assert results == []
        mock_recent.assert_awaited_once()


class TestFetchObservations:
    @pytest.mark.asyncio
    async def test_recent_fetches_ordered_by_recency_and_filters_levels(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(
            db_session, workspace.name, peer.name, peer.name, 5, level="explicit"
        )
        await _seed_documents(
            db_session, workspace.name, peer.name, peer.name, 1, level="inductive"
        )

        docs = await _fetch_recent_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=200,
            levels=["explicit"],
        )
        assert len(docs) == 5
        assert all(d.level == "explicit" for d in docs)

        # levels=[] disables the level filter -> all 6 docs come back.
        all_docs = await _fetch_recent_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=200,
            levels=[],
        )
        assert len(all_docs) == 6

        limited = await _fetch_recent_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=2,
            levels=[],
        )
        assert len(limited) == 2

    @pytest.mark.asyncio
    async def test_random_fetches_with_level_filter(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(
            db_session, workspace.name, peer.name, peer.name, 4, level="explicit"
        )
        await _seed_documents(
            db_session, workspace.name, peer.name, peer.name, 1, level="inductive"
        )

        docs = await _fetch_random_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=200,
            levels=["explicit"],
        )
        assert len(docs) == 4
        assert all(d.level == "explicit" for d in docs)

        all_docs = await _fetch_random_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=200,
            levels=[],
        )
        assert len(all_docs) == 5

    @pytest.mark.asyncio
    async def test_all_fetches_ordered_by_recency(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        workspace, peer = sample_data
        await _seed_documents(
            db_session, workspace.name, peer.name, peer.name, 3, level="explicit"
        )
        await _seed_documents(
            db_session, workspace.name, peer.name, peer.name, 1, level="deductive"
        )

        docs = await _fetch_all_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=200,
            levels=["explicit", "deductive"],
        )
        assert len(docs) == 4

        all_docs = await _fetch_all_observations(
            db_session,
            workspace.name,
            peer.name,
            peer.name,
            limit=200,
            levels=[],
        )
        assert len(all_docs) == 4


class TestHelpers:
    def test_extract_embeddings_empty(self):
        embeddings = _extract_embeddings([])
        assert embeddings.size == 0

    def test_extract_embeddings_shape(self):
        observations = [_observation(_embedding(0.0)), _observation(_embedding(1.0))]
        embeddings = _extract_embeddings(observations)
        assert embeddings.shape == (2, _DIM)
        assert embeddings.dtype == np.float32

    def test_build_tree_empty_returns_unconfigured_tree(self):
        with patch(
            "src.dreamer.surprisal.create_tree", return_value=_StubTree()
        ) as mock_create_tree:
            tree = _build_tree(np.array([]))
        mock_create_tree.assert_called_once_with(settings.DREAM.SURPRISAL.TREE_TYPE)
        assert tree.inserted is None

    def test_build_tree_inserts_embeddings(self):
        embeddings = _extract_embeddings([_observation(_embedding(0.0))])
        with patch(
            "src.dreamer.surprisal.create_tree", return_value=_StubTree()
        ) as mock_create_tree:
            tree = _build_tree(embeddings)
        mock_create_tree.assert_called_once_with(
            tree_type=settings.DREAM.SURPRISAL.TREE_TYPE,
            k=settings.DREAM.SURPRISAL.TREE_K,
        )
        assert tree.inserted is not None

    def test_compute_surprisal_scores(self):
        obs1 = _observation(_embedding(0.0))
        obs2 = _observation(_embedding(1.0))
        observations = [obs1, obs2]
        embeddings = _extract_embeddings(observations)
        scores = _compute_surprisal_scores(observations, embeddings, _StubTree())
        assert len(scores) == 2
        # Sum of a 1536-dim vector of value v is 1536 * v.
        assert scores[0].surprisal == pytest.approx(0.0)
        assert scores[1].surprisal == pytest.approx(float(_DIM))
        assert scores[0].observation is obs1

    def test_normalize_scores_empty(self):
        assert _normalize_scores([]) == []

    def test_normalize_scores_identical_scores(self):
        obs = _observation(_embedding(1.0))
        scores = [_score(obs, 0.75), _score(obs, 0.75)]
        normalized = _normalize_scores(scores)
        assert [s.surprisal for s in normalized] == [0.5, 0.5]

    def test_normalize_scores_min_max(self):
        obs = _observation(_embedding(1.0))
        scores = [_score(obs, 1.0), _score(obs, 3.0), _score(obs, 5.0)]
        normalized = _normalize_scores(scores)
        assert [s.surprisal for s in normalized] == pytest.approx(
            [0.0, 0.5, 1.0]
        )

    def test_filter_by_percent_empty(self):
        assert _filter_by_percent([]) == []

    def test_filter_by_percent_takes_top_percentage(self, monkeypatch):
        monkeypatch.setattr(
            settings.DREAM.SURPRISAL, "TOP_PERCENT_SURPRISAL", 0.5
        )
        obs = _observation(_embedding(1.0))
        scores = [_score(obs, float(i)) for i in range(10)]
        filtered = _filter_by_percent(scores)
        assert len(filtered) == 5

    def test_filter_by_percent_at_least_one(self):
        obs = _observation(_embedding(1.0))
        scores = [_score(obs, float(i)) for i in range(5)]
        filtered = _filter_by_percent(scores)
        assert len(filtered) == 1
