"""Unit tests for src/db.py pool/tracker/instrumentation/init paths."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import QueuePool

import src.db as db_module
from src.config import settings
from src.db import DBConnectionTracker


class _FakeQueuePool(QueuePool):
    def __init__(self, values=None, raise_exc=None):
        self._values = values or {}
        self._raise = raise_exc

    def checkedout(self):
        if self._raise:
            raise RuntimeError("boom")
        return self._values.get("checked_out", 0)

    def checkedin(self):
        return self._values.get("checked_in", 0)

    def size(self):
        return self._values.get("size", 0)

    def overflow(self):
        return self._values.get("overflow", 0)


def test_get_pool_stats_non_queuepool_returns_zeros(monkeypatch):
    monkeypatch.setattr(db_module.engine, "pool", object())
    assert db_module.get_pool_stats() == {
        "checked_out": 0,
        "checked_in": 0,
        "size": 0,
        "overflow": 0,
    }


def test_get_pool_stats_queuepool_values(monkeypatch):
    monkeypatch.setattr(
        db_module.engine, "pool", _FakeQueuePool(
            {"checked_out": 2, "checked_in": 3, "size": 5, "overflow": -1}
        )
    )
    stats = db_module.get_pool_stats()
    assert stats["checked_out"] == 2
    assert stats["checked_in"] == 3
    assert stats["size"] == 5
    # negative overflow clamps to 0
    assert stats["overflow"] == 0


def test_get_pool_stats_exception_returns_zeros(monkeypatch):
    monkeypatch.setattr(
        db_module.engine, "pool", _FakeQueuePool(raise_exc=True)
    )
    assert db_module.get_pool_stats() == {
        "checked_out": 0,
        "checked_in": 0,
        "size": 0,
        "overflow": 0,
    }


def _fake_gauge_child():
    return SimpleNamespace(value=[0.0], inc=lambda: None, dec=lambda: None)


def test_db_connection_tracker_connect_close():
    child_open = SimpleNamespace(v=[0.0])
    child_est = SimpleNamespace(v=[0.0])
    child_open.inc = lambda: child_open.v.__setitem__(0, child_open.v[0] + 1)
    child_open.dec = lambda: child_open.v.__setitem__(0, child_open.v[0] - 1)
    child_est.inc = lambda: child_est.v.__setitem__(0, child_est.v[0] + 1)
    tracker = DBConnectionTracker(child_open, child_est)

    record = SimpleNamespace(info={})
    tracker.on_connect(None, record)
    assert child_open.v[0] == 1
    assert child_est.v[0] == 1
    assert record.info[DBConnectionTracker.OPEN_KEY] is True

    tracker.on_close(None, record)
    assert child_open.v[0] == 0
    assert DBConnectionTracker.OPEN_KEY not in record.info


def test_db_connection_tracker_close_without_mark_no_decrement():
    child_open = SimpleNamespace(v=[0.0])
    child_open.dec = lambda: child_open.v.__setitem__(0, child_open.v[0] - 1)
    tracker = DBConnectionTracker(child_open, SimpleNamespace(v=[0.0]))
    tracker.on_close(None, SimpleNamespace(info={}))
    assert child_open.v[0] == 0


def test_db_connection_tracker_inc_exception_swallowed():
    class Bad:
        def inc(self):
            raise RuntimeError("metrics down")

    tracker = DBConnectionTracker(Bad(), Bad())
    tracker.on_connect(None, SimpleNamespace(info={}))  # must not raise


def test_db_connection_tracker_close_dec_exception_swallowed():
    class BadLive:
        def dec(self):
            raise RuntimeError("metrics down")

    record = SimpleNamespace(info={DBConnectionTracker.OPEN_KEY: True})
    tracker = DBConnectionTracker(SimpleNamespace(), BadLive())
    tracker.on_close(None, record)  # must not raise


def test_register_db_query_instrumentation_disabled_noop(monkeypatch):
    monkeypatch.setattr(settings.METRICS, "ENABLED", False)
    db_module._db_query_instrumentation_registered = False
    db_module.register_db_query_instrumentation("api")
    assert db_module._db_query_instrumentation_registered is False
    assert db_module._inflight_tracker is None


def test_register_db_query_instrumentation_enabled_idempotent(monkeypatch):
    monkeypatch.setattr(settings.METRICS, "ENABLED", True)
    db_module._db_query_instrumentation_registered = False
    db_module.register_db_query_instrumentation("api")
    assert db_module._db_query_instrumentation_registered is True
    assert db_module._inflight_tracker is not None
    # Second call returns early (no double registration).
    db_module.register_db_query_instrumentation("api")
    assert db_module._db_query_instrumentation_registered is True


def test_register_db_connection_instrumentation_disabled_noop(monkeypatch):
    monkeypatch.setattr(settings.METRICS, "ENABLED", False)
    db_module._db_connection_instrumentation_registered = False
    db_module.register_db_connection_instrumentation("api")
    assert db_module._db_connection_instrumentation_registered is False
    assert db_module._connection_tracker is None


def test_register_db_connection_instrumentation_enabled(monkeypatch):
    monkeypatch.setattr(settings.METRICS, "ENABLED", True)
    db_module._db_connection_instrumentation_registered = False
    db_module.register_db_connection_instrumentation("api")
    assert db_module._db_connection_instrumentation_registered is True
    assert db_module._connection_tracker is not None


@pytest.mark.asyncio
async def test_init_db_runs_schema_extension_and_migrations(
    monkeypatch: pytest.MonkeyPatch,
):
    import sys
    import types

    class FakeConn:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, *a, **k):
            self.executed.append(str(sql))

        async def commit(self):
            pass

    class FakeEngine:
        def connect(self):
            conn = FakeConn()

            @asynccontextmanager
            async def _cm():
                yield conn

            return _cm()

    fake_engine = FakeEngine()
    monkeypatch.setattr(db_module, "engine", fake_engine)

    upgraded = {"called": False}

    # Inject fake alembic modules so the ``from alembic import command`` and
    # ``from alembic.config import Config`` imports inside ``init_db`` resolve
    # to our stubs regardless of a ``tests/alembic`` package shadowing the real
    # ``alembic`` in a full-suite collection.
    fake_command = types.ModuleType("alembic.command")
    fake_command.upgrade = lambda cfg, rev: upgraded.update(called=True)
    fake_config = types.ModuleType("alembic.config")
    fake_config.Config = lambda *a, **k: SimpleNamespace()
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.command = fake_command
    fake_alembic.config = fake_config
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_command)
    monkeypatch.setitem(sys.modules, "alembic.config", fake_config)

    await db_module.init_db()
    assert upgraded["called"] is True


def test_request_context_default():
    assert db_module.request_context.get() is None
