"""Unit tests for src/deriver/__main__.py (process entry scaffolding)."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.deriver.__main__ as main_mod


# ---------------------------------------------------------------------------
# start_metrics_server
# ---------------------------------------------------------------------------

def test_start_metrics_server(monkeypatch):
    calls = {}

    def fake_http_server(port):
        calls["http_port"] = port

    def fake_pool_collector(instance):
        calls["pool"] = instance

    def fake_query_inst(instance):
        calls["query"] = instance

    def fake_conn_inst(instance):
        calls["conn"] = instance

    def fake_init_bounded(instance_type=None, **kw):
        calls["bounded"] = instance_type

    monkeypatch.setattr(main_mod, "start_http_server", fake_http_server)
    monkeypatch.setattr(main_mod, "register_db_pool_collector", fake_pool_collector)
    monkeypatch.setattr(main_mod, "register_db_query_instrumentation", fake_query_inst)
    monkeypatch.setattr(
        main_mod, "register_db_connection_instrumentation", fake_conn_inst
    )
    monkeypatch.setattr(
        main_mod.prometheus_metrics, "initialize_bounded_metrics", fake_init_bounded
    )

    main_mod.start_metrics_server()
    assert calls["http_port"] == 9090
    assert calls["pool"] == "deriver"
    assert calls["query"] == "deriver"
    assert calls["conn"] == "deriver"
    assert calls["bounded"] == "deriver"


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

def test_setup_logging_respects_env_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    main_mod.setup_logging()  # must not raise


def test_setup_logging_invalid_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "BOGUS")
    main_mod.setup_logging()  # must not raise


def test_setup_logging_disables_engine_logging_when_sql_debug_off(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setattr(main_mod.settings.DB, "SQL_DEBUG", False)
    main_mod.setup_logging()
    engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    assert engine_logger.disabled is True


def test_setup_logging_keeps_engine_logging_when_sql_debug_on(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setattr(main_mod.settings.DB, "SQL_DEBUG", True)
    engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    engine_logger.disabled = False  # reset global state from prior test
    main_mod.setup_logging()
    assert engine_logger.disabled is False


def test_setup_logging_sets_http_client_levels(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    main_mod.setup_logging()
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("openai._base_client").level == logging.WARNING


# ---------------------------------------------------------------------------
# run_deriver
# ---------------------------------------------------------------------------

def _patch_run_deps(monkeypatch):
    calls = {"order": []}

    async def fake_init():
        calls["order"].append("init")

    async def fake_validate(_engine):
        calls["order"].append("validate")

    async def fake_main():
        calls["order"].append("main")

    async def fake_shutdown():
        calls["order"].append("shutdown")

    monkeypatch.setattr(
        main_mod, "initialize_telemetry_async", fake_init
    )
    monkeypatch.setattr(main_mod, "validate_embedding_schema", fake_validate)
    monkeypatch.setattr(main_mod, "main", fake_main)
    monkeypatch.setattr(main_mod, "shutdown_telemetry", fake_shutdown)
    return calls


@pytest.mark.asyncio
async def test_run_deriver_lifecycle_order(monkeypatch):
    calls = _patch_run_deps(monkeypatch)
    await main_mod.run_deriver()
    assert calls["order"] == ["init", "validate", "main", "shutdown"]


@pytest.mark.asyncio
async def test_run_deriver_shutdown_called_on_validation_error(monkeypatch):
    calls = _patch_run_deps(monkeypatch)

    async def failing_validate(_engine):
        raise RuntimeError("schema mismatch")

    monkeypatch.setattr(main_mod, "validate_embedding_schema", failing_validate)

    with pytest.raises(RuntimeError, match="schema mismatch"):
        await main_mod.run_deriver()
    # finally block still flushes telemetry
    assert calls["order"][-1] == "shutdown"
