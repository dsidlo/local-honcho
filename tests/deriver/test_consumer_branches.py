"""Unit tests for src/deriver/consumer.py branch coverage.

Mocks the heavy dependencies (tracked_db, crud, webhook_delivery, summarizer,
dreamer, reconciler cycles, telemetry emit) so we can drive coverage of the
dispatch/branch logic in process_item, process_deletion, and
process_reconciler without requiring live services.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.deriver.consumer import (
    process_deletion,
    process_item,
    process_reconciler,
    process_representation_batch,
)
from src.exceptions import ValidationException
from src.schemas import ReconcilerType


def _queue_item(task_type="summary", payload=None, workspace_name="ws", message_id=1):
    return SimpleNamespace(
        task_type=task_type,
        payload=payload or {},
        workspace_name=workspace_name,
        message_id=message_id,
        id=10,
    )


async def _dummy_tracked_db(operation_name=""):
    """Return an async context manager wrapping a fake db session."""
    db = MagicMock()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    return _Ctx()


@pytest_asyncio.fixture
async def tracked_db_patch():
    """Patch tracked_db to return an async context manager wrapping a MagicMock."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    with patch("src.deriver.consumer.tracked_db", return_value=_Ctx()) as m:
        m._db = db
        yield m


class TestProcessItemDispatch:
    async def test_reconciler_task(self):
        payload = {"task_type": "reconciler", "reconciler_type": ReconcilerType.SYNC_VECTORS.value}
        with patch("src.deriver.consumer.process_reconciler", new_callable=AsyncMock) as mock_rec:
            await process_item(_queue_item("reconciler", payload, workspace_name=None))
        mock_rec.assert_awaited_once()

    async def test_reconciler_invalid_payload(self):
        with patch("src.deriver.consumer.process_reconciler", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await process_item(_queue_item("reconciler", {"bad": "x"}, workspace_name=None))

    async def test_missing_workspace_raises(self):
        with pytest.raises(ValueError):
            await process_item(_queue_item("summary", {}, workspace_name=None))

    async def test_webhook_success(self):
        payload = {"task_type": "webhook", "event_type": "message.created", "data": {}}
        with patch("src.deriver.consumer.webhook_delivery.deliver_webhook", new_callable=AsyncMock) as mock_deliver:
            await process_item(_queue_item("webhook", payload))
        mock_deliver.assert_awaited_once()

    async def test_webhook_invalid_payload(self):
        with patch("src.deriver.consumer.webhook_delivery.deliver_webhook", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await process_item(_queue_item("webhook", {"bad": 1}))

    async def test_summary_with_message_public_id(self):
        payload = {
            "task_type": "summary",
            "message_public_id": "pub",
            "session_name": "s",
            "message_seq_in_session": 1,
            "configuration": {
                "reasoning": {"enabled": True},
                "peer_card": {"use": True, "create": True},
                "summary": {
                    "enabled": True,
                    "messages_per_short_summary": 10,
                    "messages_per_long_summary": 20,
                },
                "dream": {"enabled": True},
            },
        }
        with patch("src.deriver.consumer.summarizer.summarize_if_needed", new_callable=AsyncMock) as mock_sum, patch(
            "src.deriver.consumer.log_performance_metrics"
        ) as mock_log:
            await process_item(_queue_item("summary", payload))
        mock_sum.assert_awaited_once()

    async def test_summary_message_id_none_raises(self):
        payload = {"message_public_id": "pub", "session_name": "s"}
        with pytest.raises(ValueError):
            await process_item(_queue_item("summary", payload, message_id=None))

    async def test_summary_fallback_fetches_message(self):
        payload = {
            "task_type": "summary",
            "message_public_id": None,
            "session_name": "s",
            "message_seq_in_session": 1,
            "configuration": {
                "reasoning": {"enabled": True},
                "peer_card": {"use": True, "create": True},
                "summary": {
                    "enabled": True,
                    "messages_per_short_summary": 10,
                    "messages_per_long_summary": 20,
                },
                "dream": {"enabled": True},
            },
        }
        msg = MagicMock()
        msg.public_id = "pub-from-db"

        class FakeResult:
            def scalar_one_or_none(self):
                return msg

        db = MagicMock()
        db.execute = AsyncMock(return_value=FakeResult())

        class _Ctx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *a):
                return False

        with patch("src.deriver.consumer.tracked_db", return_value=_Ctx()), patch(
            "src.deriver.consumer.summarizer.summarize_if_needed", new_callable=AsyncMock
        ) as mock_sum:
            await process_item(_queue_item("summary", payload))
        mock_sum.assert_awaited_once()

    async def test_summary_fallback_message_missing_returns(self):
        payload = {
            "task_type": "summary",
            "message_public_id": None,
            "session_name": "s",
            "message_seq_in_session": 1,
            "configuration": {
                "reasoning": {"enabled": True},
                "peer_card": {"use": True, "create": True},
                "summary": {
                    "enabled": True,
                    "messages_per_short_summary": 10,
                    "messages_per_long_summary": 20,
                },
                "dream": {"enabled": True},
            },
        }

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        db = MagicMock()
        db.execute = AsyncMock(return_value=FakeResult())

        class _Ctx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *a):
                return False

        with patch("src.deriver.consumer.tracked_db", return_value=_Ctx()), patch(
            "src.deriver.consumer.summarizer.summarize_if_needed", new_callable=AsyncMock
        ) as mock_sum:
            await process_item(_queue_item("summary", payload))
        mock_sum.assert_not_awaited()

    async def test_dream_success(self):
        payload = {"task_type": "dream", "dream_type": "omni", "observer": "o", "observed": "obs", "session_name": "s"}
        with patch("src.deriver.consumer.process_dream", new_callable=AsyncMock) as mock_dream:
            await process_item(_queue_item("dream", payload))
        mock_dream.assert_awaited_once()

    async def test_dream_invalid_payload(self):
        with patch("src.deriver.consumer.process_dream", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await process_item(_queue_item("dream", {"bad": 1}))

    async def test_deletion_success(self):
        payload = {"deletion_type": "session", "resource_id": "sess1"}
        with patch("src.deriver.consumer.process_deletion", new_callable=AsyncMock) as mock_del:
            await process_item(_queue_item("deletion", payload))
        mock_del.assert_awaited_once()

    async def test_deletion_invalid_payload(self):
        with patch("src.deriver.consumer.process_deletion", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await process_item(_queue_item("deletion", {"bad": 1}))

    async def test_scope_backfill_success(self):
        payload = {"task_type": "scope_backfill", "scope_peer": "peer1", "session_name": "s"}
        with patch("src.deriver.consumer.process_scope_backfill", new_callable=AsyncMock) as mock_bf:
            await process_item(_queue_item("scope_backfill", payload))
        mock_bf.assert_awaited_once()

    async def test_scope_backfill_invalid(self):
        with patch("src.deriver.consumer.process_scope_backfill", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await process_item(_queue_item("scope_backfill", {"bad": 1}))

    async def test_scope_removal_success(self):
        payload = {"task_type": "scope_removal", "scope_peer": "peer1", "session_name": "s"}
        with patch("src.deriver.consumer.process_scope_removal", new_callable=AsyncMock) as mock_rm:
            await process_item(_queue_item("scope_removal", payload))
        mock_rm.assert_awaited_once()

    async def test_scope_removal_invalid(self):
        with patch("src.deriver.consumer.process_scope_removal", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await process_item(_queue_item("scope_removal", {"bad": 1}))

    async def test_unknown_task_type(self):
        with pytest.raises(ValueError):
            await process_item(_queue_item("bogus", {}))


class TestProcessRepresentationBatch:
    async def test_empty_messages_returns(self):
        await process_representation_batch([], None, observers=["o"], observed="obs", queue_item_message_ids=[])

    async def test_missing_observers_raises(self):
        msg = MagicMock()
        with pytest.raises(ValueError):
            await process_representation_batch(
                [msg], None, observers=None, observed="obs", queue_item_message_ids=[1]
            )

    async def test_empty_observers_raises(self):
        msg = MagicMock()
        with pytest.raises(ValueError):
            await process_representation_batch(
                [msg], None, observers=[], observed="obs", queue_item_message_ids=[1]
            )

    async def test_delegates_to_tasks_batch(self):
        msg = MagicMock()
        with patch(
            "src.deriver.consumer.process_representation_tasks_batch", new_callable=AsyncMock
        ) as mock_batch:
            await process_representation_batch(
                [msg],
                "config",
                observers=["o1", "o2"],
                observed="obs",
                queue_item_message_ids=[1, 2],
                session_id="sess",
                queue_item_ids=[10, 11],
                hit_batch_token_cap=True,
                was_flush_enabled=True,
                batch_max_tokens=500,
            )
        kwargs = mock_batch.await_args.kwargs
        assert kwargs["observers"] == ["o1", "o2"]
        assert kwargs["observed"] == "obs"
        assert kwargs["session_id"] == "sess"
        assert kwargs["hit_batch_token_cap"] is True


class TestProcessDeletion:
    def _deletion_payload(self, deletion_type="session", resource_id="sess1"):
        return SimpleNamespace(deletion_type=deletion_type, resource_id=resource_id)

    async def test_delete_session_success(self, tracked_db_patch):
        result = SimpleNamespace(messages_deleted=3, conclusions_deleted=2)
        db = tracked_db_patch._db
        crud_del = AsyncMock(return_value=result)
        with patch("src.deriver.consumer.crud.delete_session", new=crud_del), patch(
            "src.deriver.consumer.emit"
        ) as mock_emit:
            await process_deletion(self._deletion_payload("session"), "ws")
        crud_del.assert_awaited_once()
        assert mock_emit.called
        event = mock_emit.call_args[0][0]
        assert event.success is True
        assert event.messages_deleted == 3

    async def test_delete_session_not_found(self, tracked_db_patch):
        from src.exceptions import ResourceNotFoundException
        with patch(
            "src.deriver.consumer.crud.delete_session",
            new=AsyncMock(side_effect=ResourceNotFoundException("gone")),
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_deletion(self._deletion_payload("session"), "ws")
        assert mock_emit.called
        assert mock_emit.call_args[0][0].success is True

    async def test_delete_observation_success(self, tracked_db_patch):
        with patch(
            "src.deriver.consumer.crud.delete_document_by_id", new=AsyncMock()
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_deletion(self._deletion_payload("observation", "doc1"), "ws")
        assert mock_emit.call_args[0][0].conclusions_deleted == 1

    async def test_delete_workspace_success(self, tracked_db_patch):
        result = SimpleNamespace(
            peers_deleted=1, sessions_deleted=2, messages_deleted=3, conclusions_deleted=4
        )
        with patch(
            "src.deriver.consumer.crud.delete_workspace", new=AsyncMock(return_value=result)
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_deletion(self._deletion_payload("workspace"), "ws")
        event = mock_emit.call_args[0][0]
        assert event.peers_deleted == 1
        assert event.sessions_deleted == 2
        assert event.success is True

    async def test_unsupported_type_raises_and_emits(self, tracked_db_patch):
        with patch("src.deriver.consumer.emit") as mock_emit:
            with pytest.raises(ValidationException):
                await process_deletion(self._deletion_payload("nope"), "ws")
        assert mock_emit.called
        assert mock_emit.call_args[0][0].success is False

    async def test_unexpected_crud_error_raises_and_emits(self, tracked_db_patch):
        with patch(
            "src.deriver.consumer.crud.delete_session",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch("src.deriver.consumer.emit") as mock_emit:
            with pytest.raises(RuntimeError):
                await process_deletion(self._deletion_payload("session"), "ws")
        event = mock_emit.call_args[0][0]
        assert event.success is False
        assert "RuntimeError" in event.error_message


class TestProcessReconciler:
    def _payload(self, rtype):
        return SimpleNamespace(reconciler_type=rtype)

    async def test_sync_vectors_emits_when_work(self):
        metrics = SimpleNamespace(
            total_synced=1, total_failed=0, total_cleaned=0,
            documents_synced=1, documents_failed=0, documents_cleaned=0,
            message_embeddings_synced=0, message_embeddings_failed=0,
        )
        with patch(
            "src.deriver.consumer.run_vector_reconciliation_cycle", new=AsyncMock(return_value=metrics)
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_reconciler(self._payload(ReconcilerType.SYNC_VECTORS))
        assert mock_emit.called

    async def test_sync_vectors_no_work_no_emit(self):
        metrics = SimpleNamespace(
            total_synced=0, total_failed=0, total_cleaned=0,
            documents_synced=0, documents_failed=0, documents_cleaned=0,
            message_embeddings_synced=0, message_embeddings_failed=0,
        )
        with patch(
            "src.deriver.consumer.run_vector_reconciliation_cycle", new=AsyncMock(return_value=metrics)
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_reconciler(self._payload(ReconcilerType.SYNC_VECTORS))
        assert not mock_emit.called

    async def test_cleanup_queue_emits_when_deleted(self):
        with patch(
            "src.deriver.consumer.cleanup_queue_items", new=AsyncMock(return_value=5)
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_reconciler(self._payload(ReconcilerType.CLEANUP_QUEUE))
        assert mock_emit.called

    async def test_cleanup_queue_no_deletions_no_emit(self):
        with patch(
            "src.deriver.consumer.cleanup_queue_items", new=AsyncMock(return_value=0)
        ), patch("src.deriver.consumer.emit") as mock_emit:
            await process_reconciler(self._payload(ReconcilerType.CLEANUP_QUEUE))
        assert not mock_emit.called

    async def test_backfill_document_sources(self):
        with patch(
            "src.deriver.consumer.run_document_sources_backfill_cycle", new=AsyncMock()
        ) as mock_bf:
            await process_reconciler(self._payload(ReconcilerType.BACKFILL_DOCUMENT_SOURCES))
        mock_bf.assert_awaited_once()

    async def test_unsupported_type_raises(self):
        with pytest.raises(ValueError):
            await process_reconciler(self._payload("bogus"))
