"""Unit tests for src/reconciler/backfill_document_sources.py."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db import Base
from src.reconciler import backfill_document_sources as mod


def test_qualified_public_schema(monkeypatch):
    monkeypatch.setattr(Base.metadata, "schema", None)
    assert mod._qualified("documents") == '"documents"'


def test_qualified_named_schema(monkeypatch):
    monkeypatch.setattr(Base.metadata, "schema", "honcho")
    assert mod._qualified("documents") == '"honcho"."documents"'


def test_drain_batch_sql_contains_expected_parts():
    sql = mod._drain_batch_sql()
    assert "INSERT INTO" in sql
    assert "document_sources" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert "source_ids" in sql
    assert "premise_ids" in sql
    assert "batch_size" in sql


@pytest.mark.asyncio
async def test_has_pending_document_sources_true():
    db = MagicMock()
    db.execute = AsyncMock()
    db.execute.return_value = MagicMock(first=lambda: ("row",))
    assert await mod.has_pending_document_sources(db) is True


@pytest.mark.asyncio
async def test_has_pending_document_sources_false():
    db = MagicMock()
    db.execute = AsyncMock()
    db.execute.return_value = MagicMock(first=lambda: None)
    assert await mod.has_pending_document_sources(db) is False


@pytest.mark.asyncio
async def test_drain_batch_returns_rowcount():
    db = MagicMock()
    db.execute = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=42)
    assert await mod.drain_document_sources_batch(db, batch_size=100) == 42
    db.execute.assert_awaited_once()
    args, _kwargs = db.execute.await_args
    assert args[1]["batch_size"] == 100


def _fake_tracked_db(counts):
    db = MagicMock()
    db.execute = AsyncMock()

    def _execute(*a, **k):
        n = next(counts, 0)
        res = MagicMock()
        res.rowcount = n
        return res

    db.execute.side_effect = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _tracked(name, **kw):
        yield db

    return db, _tracked


@pytest.mark.asyncio
async def test_run_cycle_drains_until_zero(monkeypatch):
    counts = iter([5, 3, 0])
    db, tracked = _fake_tracked_db(counts)
    monkeypatch.setattr(mod, "tracked_db", tracked)
    assert await mod.run_document_sources_backfill_cycle() == 8
    assert db.commit.await_count == 3


@pytest.mark.asyncio
async def test_run_cycle_zero_first_batch_returns_zero(monkeypatch):
    counts = iter([0])
    db, tracked = _fake_tracked_db(counts)
    monkeypatch.setattr(mod, "tracked_db", tracked)
    assert await mod.run_document_sources_backfill_cycle() == 0
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_run_cycle_stops_when_budget_expired(monkeypatch):
    # Always returns nonzero, but the budget expires before the next batch.
    counts = iter([10, 10, 10])
    db, tracked = _fake_tracked_db(counts)
    monkeypatch.setattr(mod, "tracked_db", tracked)
    clock = {"now": 100.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(mod, "BACKFILL_TIME_BUDGET_SECONDS", 1.0)

    real_execute = db.execute.side_effect
    calls = {"n": 0}

    def _bounded_execute(*a, **k):
        nonlocal calls
        calls["n"] += 1
        # Jump past the deadline after two batches so the loop exits by budget.
        if calls["n"] >= 2:
            clock["now"] = 200.0
        return real_execute(*a, **k)

    db.execute.side_effect = _bounded_execute
    assert await mod.run_document_sources_backfill_cycle() == 20
