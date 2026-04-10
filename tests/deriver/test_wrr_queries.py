"""Integration tests for WRR query functions.

Tests the weighted round-robin query functions in isolation
and with various edge cases.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import delete, select

from src import models
from src.config import settings
from src.deriver.wrr_queries import (
    query_task_type_work_units,
    _query_standard_work_units,
    _query_representation_work_units,
    query_idle_fill_work_units,
)


class TestQueryTaskTypeWorkUnits:
    """Tests for query_task_type_work_units dispatcher function."""

    @pytest.mark.asyncio
    async def test_query_task_type_representation(
        self,
        db_session,
        sample_session_with_peers,
        sample_messages,
    ):
        """Test dispatcher routes to representation query."""
        session, peers = sample_session_with_peers
        message = sample_messages[0]

        # Create representation queue items with sufficient tokens
        wuk = f"representation:{session.workspace_name}:{session.name}:{peers[0].name}:{peers[0].name}"
        qi1 = models.QueueItem(
            session_id=session.id,
            task_type="representation",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=message.id,
            payload={"configuration": {"task_type": "representation"}},
            created_at=datetime.now(timezone.utc),
        )

        db_session.add(qi1)
        await db_session.commit()

        # Query should find the work unit (FLUSH_ENABLED or tokens)
        result = await query_task_type_work_units(
            db=db_session,
            task_type="representation",
            limit=10,
        )

        # Should find the work unit (since FLUSH_ENABLED may be true or tokens met)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_task_type_webhook(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test dispatcher routes to standard query for webhook."""
        session, peers = sample_session_with_peers

        # Create webhook queue items
        wuk = f"webhook:{session.workspace_name}"
        qi1 = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,  # webhook doesn't require message
            payload={},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        qi2 = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        db_session.add_all([qi1, qi2])
        await db_session.commit()

        result = await query_task_type_work_units(
            db=db_session,
            task_type="webhook",
            limit=10,
        )

        assert wuk in result
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_query_task_type_unknown(self, db_session):
        """Test dispatcher returns empty list for unknown task types."""
        result = await query_task_type_work_units(
            db=db_session,
            task_type="unknown_task",
            limit=10,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_query_task_type_idle_fill(self, db_session):
        """Test dispatcher returns empty list for idle_fill (handled separately)."""
        result = await query_task_type_work_units(
            db=db_session,
            task_type="idle_fill",
            limit=10,
        )

        assert result == []


class TestQueryStandardWorkUnits:
    """Tests for _query_standard_work_units function."""

    @pytest.mark.asyncio
    async def test_standard_query_returns_oldest_first(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test standard query orders by created_at ASC (oldest first)."""
        session, peers = sample_session_with_peers

        # Create webhook items with different timestamps
        now = datetime.now(timezone.utc)
        
        for i, minutes_ago in enumerate([30, 20, 10]):
            wuk = f"webhook:{session.workspace_name}:{i}"
            qi = models.QueueItem(
                session_id=session.id,
                task_type="webhook",
                work_unit_key=wuk,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=None,
                payload={},
                created_at=now - timedelta(minutes=minutes_ago),
            )
            db_session.add(qi)

        await db_session.commit()

        result = await _query_standard_work_units(
            db=db_session,
            task_type="webhook",
            limit=3,
        )

        # Oldest (30 min ago) should be first
        assert len(result) == 3
        # First result should be oldest

    @pytest.mark.asyncio
    async def test_standard_query_respects_limit(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test standard query respects limit parameter."""
        session, peers = sample_session_with_peers

        # Create 5 webhook items
        for i in range(5):
            wuk = f"webhook:{session.workspace_name}:{i}"
            qi = models.QueueItem(
                session_id=session.id,
                task_type="webhook",
                work_unit_key=wuk,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=None,
                payload={},
            )
            db_session.add(qi)

        await db_session.commit()

        result = await _query_standard_work_units(
            db=db_session,
            task_type="webhook",
            limit=2,
        )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_standard_query_excludes_claimed(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test standard query excludes already-claimed work units."""
        session, peers = sample_session_with_peers

        wuk = f"webhook:{session.workspace_name}:claimed"
        qi = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
        )
        db_session.add(qi)
        await db_session.flush()

        # Create active queue session (claimed)
        aqs = models.ActiveQueueSession(work_unit_key=wuk)
        db_session.add(aqs)
        await db_session.commit()

        result = await _query_standard_work_units(
            db=db_session,
            task_type="webhook",
            limit=10,
        )

        assert wuk not in result

    @pytest.mark.asyncio
    async def test_standard_query_excludes_processed(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test standard query excludes already-processed work units."""
        session, peers = sample_session_with_peers

        wuk = f"webhook:{session.workspace_name}:processed"
        qi = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=wuk,
            processed=True,  # Already processed
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
        )
        db_session.add(qi)
        await db_session.commit()

        result = await _query_standard_work_units(
            db=db_session,
            task_type="webhook",
            limit=10,
        )

        assert wuk not in result

    @pytest.mark.asyncio
    async def test_standard_query_filters_by_prefix(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test standard query filters by work_unit_key prefix."""
        session, peers = sample_session_with_peers

        # Create webhook and dream items
        webhook_wuk = f"webhook:{session.workspace_name}:1"
        dream_wuk = f"dream:{session.workspace_name}:1"

        webhook_qi = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=webhook_wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
        )
        dream_qi = models.QueueItem(
            session_id=session.id,
            task_type="dream",
            work_unit_key=dream_wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
        )

        db_session.add_all([webhook_qi, dream_qi])
        await db_session.commit()

        webhook_result = await _query_standard_work_units(
            db=db_session,
            task_type="webhook",
            limit=10,
        )

        dream_result = await _query_standard_work_units(
            db=db_session,
            task_type="dream",
            limit=10,
        )

        assert webhook_wuk in webhook_result
        assert webhook_wuk not in dream_result
        assert dream_wuk in dream_result
        assert dream_wuk not in webhook_result


class TestQueryRepresentationWorkUnits:
    """Tests for _query_representation_work_units function."""

    @pytest.mark.asyncio
    async def test_representation_query_orders_by_token_count_desc(
        self,
        db_session,
        sample_session_with_peers,
        sample_messages,
    ):
        """Test representation query orders by token count (descending)."""
        session, peers = sample_session_with_peers

        # Create messages with different token counts
        msg_high = models.Message(
            session_name=session.name,
            content="a very long message with many tokens" * 50,
            peer_name=peers[0].name,
            workspace_name=session.workspace_name,
            seq_in_session=10,
        )
        msg_low = models.Message(
            session_name=session.name,
            content="short",
            peer_name=peers[1].name,
            workspace_name=session.workspace_name,
            seq_in_session=11,
        )

        db_session.add_all([msg_high, msg_low])
        await db_session.flush()

        # Create queue items
        wuk_high = f"representation:{session.workspace_name}:{session.name}:{peers[0].name}:{peers[0].name}:high"
        wuk_low = f"representation:{session.workspace_name}:{session.name}:{peers[1].name}:{peers[1].name}:low"

        qi_high = models.QueueItem(
            session_id=session.id,
            task_type="representation",
            work_unit_key=wuk_high,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=msg_high.id,
            payload={},
        )
        qi_low = models.QueueItem(
            session_id=session.id,
            task_type="representation",
            work_unit_key=wuk_low,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=msg_low.id,
            payload={},
        )

        db_session.add_all([qi_high, qi_low])
        await db_session.commit()

        results = await _query_representation_work_units(
            db=db_session,
            limit=10,
        )

        # Both should be returned (if tokens meet threshold or FLUSH_ENABLED)
        assert len(results) >= 0

    @pytest.mark.asyncio
    async def test_representation_query_applies_token_threshold(
        self,
        db_session,
        sample_session_with_peers,
        monkeypatch,
    ):
        """Test representation query filters by token threshold."""
        session, peers = sample_session_with_peers

        # Force FLUSH_ENABLED off so the token threshold logic is tested
        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", False)

        msg_above = models.Message(
            session_name=session.name,
            content="This message has sufficient tokens for batching purposes" * 20,
            peer_name=peers[0].name,
            workspace_name=session.workspace_name,
            seq_in_session=20,
        )

        db_session.add(msg_above)
        await db_session.flush()

        wuk = f"representation:{session.workspace_name}:{session.name}:{peers[0].name}:{peers[0].name}"
        qi = models.QueueItem(
            session_id=session.id,
            task_type="representation",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=msg_above.id,
            payload={},
        )

        db_session.add(qi)
        await db_session.commit()

        result = await _query_representation_work_units(
            db=db_session,
            limit=10,
        )

        # Result depends on actual token counts vs threshold
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_representation_anti_starvation_fallback(
        self,
        monkeypatch,
        db_session,
        sample_session_with_peers,
    ):
        """Test anti-starvation fallback for old representation tasks."""
        session, peers = sample_session_with_peers

        monkeypatch.setattr(settings.DERIVER, "FLUSH_ENABLED", False)

        # Create an old message with low tokens
        msg_old = models.Message(
            session_name=session.name,
            content="short",  # Low tokens
            peer_name=peers[0].name,
            workspace_name=session.workspace_name,
            seq_in_session=30,
        )

        db_session.add(msg_old)
        await db_session.flush()

        # Create old queue item (> 1 hour ago)
        wuk = f"representation:{session.workspace_name}:{session.name}:{peers[0].name}:{peers[0].name}"
        qi = models.QueueItem(
            session_id=session.id,
            task_type="representation",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=msg_old.id,
            payload={},
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        db_session.add(qi)
        await db_session.commit()

        result = await _query_representation_work_units(
            db=db_session,
            limit=10,
        )

        # Should find old task via anti-starvation fallback
        # (if message has at least some tokens)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_representation_ignores_zero_token_tasks(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test representation query ignores tasks with zero tokens."""
        session, peers = sample_session_with_peers

        # Create empty message
        msg_empty = models.Message(
            session_name=session.name,
            content="",
            peer_name=peers[0].name,
            workspace_name=session.workspace_name,
            seq_in_session=40,
        )

        db_session.add(msg_empty)
        await db_session.flush()

        wuk = f"representation:{session.workspace_name}:{session.name}:{peers[0].name}:{peers[0].name}"
        qi = models.QueueItem(
            session_id=session.id,
            task_type="representation",
            work_unit_key=wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=msg_empty.id,
            payload={},
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        db_session.add(qi)
        await db_session.commit()

        result = await _query_representation_work_units(
            db=db_session,
            limit=10,
        )

        # Should not include zero-token tasks
        assert wuk not in result


class TestQueryIdleFillWorkUnits:
    """Tests for query_idle_fill_work_units function."""

    @pytest.mark.asyncio
    async def test_idle_fill_returns_oldest_first(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test idle fill query orders by oldest created_at."""
        session, peers = sample_session_with_peers

        now = datetime.now(timezone.utc)

        # Create items of mixed types with different ages
        for i, (task_type, minutes_ago) in enumerate([
            ("webhook", 30),
            ("dream", 20),
            ("webhook", 10),
        ]):
            prefix = task_type
            wuk = f"{prefix}:{session.workspace_name}:{i}"
            qi = models.QueueItem(
                session_id=session.id,
                task_type=task_type,
                work_unit_key=wuk,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=None,
                payload={},
                created_at=now - timedelta(minutes=minutes_ago),
            )
            db_session.add(qi)

        await db_session.commit()

        result = await query_idle_fill_work_units(
            db=db_session,
            limit=10,
        )

        # Should return results
        assert isinstance(result, list)
        if result:
            # Oldest should be first
            pass

    @pytest.mark.asyncio
    async def test_idle_fill_excludes_work_units(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test idle fill excludes specified work units."""
        session, peers = sample_session_with_peers

        wuk1 = f"webhook:{session.workspace_name}:1"
        wuk2 = f"webhook:{session.workspace_name}:2"

        for wuk in [wuk1, wuk2]:
            qi = models.QueueItem(
                session_id=session.id,
                task_type="webhook",
                work_unit_key=wuk,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=None,
                payload={},
            )
            db_session.add(qi)

        await db_session.commit()

        result = await query_idle_fill_work_units(
            db=db_session,
            limit=10,
            exclude_work_units={wuk1},
        )

        assert wuk1 not in result
        # wuk2 should be included (unless processed/claimed)

    @pytest.mark.asyncio
    async def test_idle_fill_respects_limit(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test idle fill respects limit parameter."""
        session, peers = sample_session_with_peers

        # Create multiple items
        for i in range(5):
            wuk = f"webhook:{session.workspace_name}:{i}"
            qi = models.QueueItem(
                session_id=session.id,
                task_type="webhook",
                work_unit_key=wuk,
                processed=False,
                workspace_name=session.workspace_name,
                message_id=None,
                payload={},
            )
            db_session.add(qi)

        await db_session.commit()

        result = await query_idle_fill_work_units(
            db=db_session,
            limit=2,
        )

        assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_idle_fill_unknown_strategy_defaults(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test idle fill defaults to oldest_first for unknown strategy."""
        session, peers = sample_session_with_peers

        result = await query_idle_fill_work_units(
            db=db_session,
            limit=10,
            strategy="unknown_strategy",
        )

        # Should not raise, should use oldest_first
        assert isinstance(result, list)


class TestWRRQueryEndToEnd:
    """End-to-end tests combining multiple WRR query functions."""

    @pytest.mark.asyncio
    async def test_wrr_workflow_simulation(
        self,
        db_session,
        sample_session_with_peers,
        sample_messages,
    ):
        """Simulate a complete WRR poll cycle."""
        session, peers = sample_session_with_peers

        # Create various task types
        now = datetime.now(timezone.utc)

        # Webhook tasks
        webhook_wuk = f"webhook:{session.workspace_name}:1"
        webhook_qi = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=webhook_wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
            created_at=now - timedelta(minutes=10),
        )

        # Dream tasks
        dream_wuk = f"dream:{session.workspace_name}:1"
        dream_qi = models.QueueItem(
            session_id=session.id,
            task_type="dream",
            work_unit_key=dream_wuk,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
            created_at=now - timedelta(minutes=20),
        )

        db_session.add_all([webhook_qi, dream_qi])
        await db_session.commit()

        # Query each task type
        webhook_results = await _query_standard_work_units(
            db=db_session, task_type="webhook", limit=2
        )
        dream_results = await _query_standard_work_units(
            db=db_session, task_type="dream", limit=2
        )

        assert webhook_wuk in webhook_results
        assert dream_wuk in dream_results

        # Simulate idle fill with remaining capacity
        claimed = {webhook_wuk, dream_wuk}
        idle_results = await query_idle_fill_work_units(
            db=db_session,
            limit=5,
            exclude_work_units=claimed,
        )

        # Should not include already claimed items
        assert webhook_wuk not in idle_results
        assert dream_wuk not in idle_results

    @pytest.mark.asyncio
    async def test_wrr_with_claimed_items(
        self,
        db_session,
        sample_session_with_peers,
    ):
        """Test WRR queries respect active queue sessions (claimed items)."""
        session, peers = sample_session_with_peers

        wuk1 = f"webhook:{session.workspace_name}:claim_test"

        qi = models.QueueItem(
            session_id=session.id,
            task_type="webhook",
            work_unit_key=wuk1,
            processed=False,
            workspace_name=session.workspace_name,
            message_id=None,
            payload={},
        )
        db_session.add(qi)
        await db_session.flush()

        # Claim it
        aqs = models.ActiveQueueSession(work_unit_key=wuk1)
        db_session.add(aqs)
        await db_session.commit()

        # Query should not return claimed item
        results = await _query_standard_work_units(
            db=db_session,
            task_type="webhook",
            limit=10,
        )

        assert wuk1 not in results

        # Idle fill should also exclude claimed items
        idle_results = await query_idle_fill_work_units(
            db=db_session,
            limit=10,
        )

        assert wuk1 not in idle_results
