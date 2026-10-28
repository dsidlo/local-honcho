# pyright: reportPrivateUsage=false
"""Queue-level rate-limit deferral (Phase 5 / Phase 6).

A provider 429 must leave the queue record unprocessed and un-errored, release
the work unit so the worker moves on, and record `rate_limit_since` — never
falling through to the terminal error path.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import openai
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.deriver.queue_manager import QueueManager, WorkerOwnership
from src.utils.work_unit import construct_work_unit_key


def _rate_limit_error() -> openai.RateLimitError:
    import httpx

    request = httpx.Request("POST", "http://tentacruel-litellm.internal/v1/messages")
    return openai.RateLimitError(
        "Rate limit reached: input token limit exceeded",
        response=httpx.Response(429, request=request, text="rate limited"),
        body=None,
    )


@pytest.mark.asyncio
class TestRateLimitDeferral:
    async def _seed_work_unit(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        n_messages: int = 2,
    ) -> tuple[QueueManager, str, str, list[models.QueueItem]]:
        session, peers = sample_session_with_peers
        peer = peers[0]

        messages: list[models.Message] = []
        for index in range(n_messages):
            message = models.Message(
                session_name=session.name,
                workspace_name=session.workspace_name,
                peer_name=peer.name,
                content=f"Message {index}",
                token_count=10,
                seq_in_session=index + 1,
            )
            db_session.add(message)
            messages.append(message)
        await db_session.commit()
        for message in messages:
            await db_session.refresh(message)

        queue_items: list[models.QueueItem] = []
        work_unit_key = ""
        for message in messages:
            payload = create_queue_payload(
                message=message,
                task_type="representation",
                observed=peer.name,
                observer=peer.name,
            )
            work_unit_key = work_unit_key or construct_work_unit_key(
                session.workspace_name, payload
            )
            queue_item = models.QueueItem(
                session_id=session.id,
                task_type="representation",
                work_unit_key=work_unit_key,
                payload=payload,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=message.id,
            )
            db_session.add(queue_item)
            queue_items.append(queue_item)
        await db_session.commit()
        for queue_item in queue_items:
            await db_session.refresh(queue_item)

        qm = QueueManager()
        worker_id = "test_worker"
        claimed_units = await qm.claim_work_units(db_session, [work_unit_key])
        qm.worker_ownership[worker_id] = WorkerOwnership(
            work_unit_key=work_unit_key, aqs_id=claimed_units[work_unit_key]
        )
        await db_session.commit()
        return qm, work_unit_key, worker_id, queue_items

    async def _fetch_items(
        self, db_session: AsyncSession, work_unit_key: str
    ) -> list[models.QueueItem]:
        db_session.expire_all()
        return list(
            (
                await db_session.execute(
                    select(models.QueueItem)
                    .where(models.QueueItem.work_unit_key == work_unit_key)
                    .order_by(models.QueueItem.id)
                )
            )
            .scalars()
            .all()
        )

    async def _aqs_rows(self, db_session: AsyncSession, work_unit_key: str) -> int:
        return len(
            (
                await db_session.execute(
                    select(models.ActiveQueueSession).where(
                        models.ActiveQueueSession.work_unit_key == work_unit_key
                    )
                )
            )
            .scalars()
            .all()
        )

    async def _retry_attempts_on_items(
        self, db_session: AsyncSession, work_unit_key: str
    ) -> int | None:
        items = await self._fetch_items(db_session, work_unit_key)
        unprocessed = [item for item in items if not item.processed]
        if not unprocessed:
            return None
        raw = (unprocessed[0].payload or {}).get("_retry_attempts")
        return None if raw is None else int(raw)

    async def test_rate_limit_defers_and_releases(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 429 leaves items unprocessed/un-errored, releases the work unit,
        increments the shared counter, and records `rate_limit_since`."""
        monkeypatch.setattr("src.deriver.queue_manager.RETRY_BACKOFF_SECONDS", 0.0)
        qm, work_unit_key, worker_id, _ = await self._seed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )
        initial_semaphore_value = qm.semaphore._value

        with patch(
            "src.deriver.queue_manager.process_representation_batch",
            side_effect=_rate_limit_error(),
        ):
            await qm.process_work_unit(work_unit_key, worker_id)

        items = await self._fetch_items(db_session, work_unit_key)
        assert all(not item.processed for item in items)
        assert all(item.error is None for item in items)
        assert await self._aqs_rows(db_session, work_unit_key) == 0
        assert await self._retry_attempts_on_items(db_session, work_unit_key) == 1
        assert qm.semaphore._value == initial_semaphore_value

        record = qm._rate_limit_tracker.get_record(work_unit_key)
        assert record is not None
        assert record.rate_limit_since is not None

    async def test_rate_limit_never_marks_errored_at_deferral_cap(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even past LLM_RETRY_RATELIMIT_MAX_DEFERRALS, a rate limit is a
        throttle, not a failure: the record stays queued, never errored."""
        from src.config import settings

        monkeypatch.setattr("src.deriver.queue_manager.RETRY_BACKOFF_SECONDS", 0.0)
        qm, work_unit_key, worker_id, _ = await self._seed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload, n_messages=1
        )
        max_deferrals = settings.LLM.RETRY_RATELIMIT_MAX_DEFERRALS
        await qm._set_work_unit_retry_attempts(work_unit_key, max_deferrals - 1)

        with patch(
            "src.deriver.queue_manager.process_representation_batch",
            side_effect=_rate_limit_error(),
        ):
            await qm.process_work_unit(work_unit_key, worker_id)

        items = await self._fetch_items(db_session, work_unit_key)
        assert len(items) == 1
        assert not items[0].processed
        assert items[0].error is None
        # The counter advanced to the cap; the record is still queued.
        assert await self._retry_attempts_on_items(db_session, work_unit_key) == (
            max_deferrals
        )
        assert await self._aqs_rows(db_session, work_unit_key) == 0

    async def test_handle_processing_error_429_branch_returns_true(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
    ) -> None:
        """Direct unit check of the 429 branch: returns True, no errored mark,
        counter incremented, and the rate-limit stamp is recorded."""
        qm, work_unit_key, worker_id, items = await self._seed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload, n_messages=1
        )

        released = await qm._handle_processing_error(
            _rate_limit_error(),
            items,
            work_unit_key,
            "processing representation batch",
        )

        assert released is True
        assert items[0].error is None
        assert not items[0].processed
        assert await self._retry_attempts_on_items(db_session, work_unit_key) == 1
        record = qm._rate_limit_tracker.get_record(work_unit_key)
        assert record is not None
        assert record.rate_limit_since is not None

    async def _seed_unclaimed_work_unit(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        n_messages: int = 2,
    ) -> tuple[QueueManager, str, list[models.QueueItem]]:
        """Seed queue items for a work unit WITHOUT claiming it, so it is
        eligible to appear in the claim path's available set."""
        session, peers = sample_session_with_peers
        peer = peers[0]

        messages: list[models.Message] = []
        for index in range(n_messages):
            message = models.Message(
                session_name=session.name,
                workspace_name=session.workspace_name,
                peer_name=peer.name,
                content=f"Message {index}",
                token_count=10,
                seq_in_session=index + 1,
            )
            db_session.add(message)
            messages.append(message)
        await db_session.commit()
        for message in messages:
            await db_session.refresh(message)

        queue_items: list[models.QueueItem] = []
        work_unit_key = ""
        for message in messages:
            payload = create_queue_payload(
                message=message,
                task_type="representation",
                observed=peer.name,
                observer=peer.name,
            )
            work_unit_key = work_unit_key or construct_work_unit_key(
                session.workspace_name, payload
            )
            queue_item = models.QueueItem(
                session_id=session.id,
                task_type="representation",
                work_unit_key=work_unit_key,
                payload=payload,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=message.id,
            )
            db_session.add(queue_item)
            queue_items.append(queue_item)
        await db_session.commit()
        for queue_item in queue_items:
            await db_session.refresh(queue_item)

        qm = QueueManager()
        return qm, work_unit_key, queue_items

    async def test_claim_path_defers_rate_limited_unit_fifo(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A unit inside its rate-limit cooldown is not claimed by the FIFO
        claim path (Phase 5 gate)."""
        from src.config import settings

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", True)
        qm, work_unit_key, _ = await self._seed_unclaimed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )
        qm._rate_limit_tracker.mark_rate_limit(work_unit_key)
        assert qm._rate_limit_tracker.is_rate_limit_cooldown_active(work_unit_key)

        claimed = await qm._get_and_claim_work_units_fifo()
        assert work_unit_key not in claimed
        assert claimed == {}

    async def test_claim_path_claims_after_cooldown_elapsed_fifo(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once the cooldown has elapsed, the unit is claimed again by the
        FIFO claim path."""
        from src.config import settings

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", True)
        qm, work_unit_key, _ = await self._seed_unclaimed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )
        qm._rate_limit_tracker.mark_rate_limit(work_unit_key)
        cooldown = settings.LLM.RETRY_RATELIMIT_COOLDOWN_SEC
        record = qm._rate_limit_tracker.get_record(work_unit_key)
        assert record is not None
        # Backdate the stamp past the cooldown window.
        record.rate_limit_since = datetime.now(UTC) - timedelta(
            seconds=cooldown + 5
        )
        assert not qm._rate_limit_tracker.is_rate_limit_cooldown_active(
            work_unit_key
        )

        claimed = await qm._get_and_claim_work_units_fifo()
        assert work_unit_key in claimed

    async def test_claim_path_claims_unit_without_rate_limit_fifo(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A unit with no rate_limit_since stamp is claimed normally by the
        FIFO claim path."""
        from src.config import settings

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", True)
        qm, work_unit_key, _ = await self._seed_unclaimed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )

        claimed = await qm._get_and_claim_work_units_fifo()
        assert work_unit_key in claimed

    async def test_claim_path_defers_rate_limited_unit_weighted(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A unit inside its rate-limit cooldown is not claimed by the
        weighted (WRR) claim path (Phase 5 gate)."""
        from src.config import settings

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", True)
        qm, work_unit_key, _ = await self._seed_unclaimed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )
        qm._rate_limit_tracker.mark_rate_limit(work_unit_key)
        assert qm._rate_limit_tracker.is_rate_limit_cooldown_active(work_unit_key)

        claimed = await qm._get_and_claim_work_units_weighted()
        assert work_unit_key not in claimed
        assert claimed == {}

    async def test_claim_path_claims_after_cooldown_elapsed_weighted(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once the cooldown has elapsed, the unit is claimed again by the
        weighted (WRR) claim path."""
        from src.config import settings

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", True)
        qm, work_unit_key, _ = await self._seed_unclaimed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )
        qm._rate_limit_tracker.mark_rate_limit(work_unit_key)
        cooldown = settings.LLM.RETRY_RATELIMIT_COOLDOWN_SEC
        record = qm._rate_limit_tracker.get_record(work_unit_key)
        assert record is not None
        record.rate_limit_since = datetime.now(UTC) - timedelta(
            seconds=cooldown + 5
        )
        assert not qm._rate_limit_tracker.is_rate_limit_cooldown_active(
            work_unit_key
        )

        claimed = await qm._get_and_claim_work_units_weighted()
        assert work_unit_key in claimed

    async def test_claim_path_claims_unit_without_rate_limit_weighted(
        self,
        db_session: AsyncSession,
        sample_session_with_peers: tuple[models.Session, list[models.Peer]],
        create_queue_payload: Callable[..., Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A unit with no rate_limit_since stamp is claimed normally by the
        weighted (WRR) claim path."""
        from src.config import settings

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", True)
        qm, work_unit_key, _ = await self._seed_unclaimed_work_unit(
            db_session, sample_session_with_peers, create_queue_payload
        )

        claimed = await qm._get_and_claim_work_units_weighted()
        assert work_unit_key in claimed
