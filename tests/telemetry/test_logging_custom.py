"""Unit tests for src/telemetry/logging.py."""

import datetime
import sys
import types
from unittest.mock import MagicMock
import pytest

from src.config import settings
from src.telemetry import logging as log_mod
from src.telemetry.logging import (
    accumulate_metric,
    flush_langfuse,
    format_reasoning_inputs_as_markdown,
    get_route_template,
    log_performance_metrics,
    log_token_usage_metrics,
    normalize_template_path,
)
from src.utils.representation import Representation


@pytest.fixture(autouse=True)
def log_mod_clean():
    log_mod.accumulated_metrics.clear()
    yield
    log_mod.accumulated_metrics.clear()


def test_flush_langfuse_noop_without_key(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    flush_langfuse()


def test_flush_langfuse_exception_handled(monkeypatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "key")
    fake_module = types.ModuleType("langfuse")
    fake_module.get_client = MagicMock(side_effect=Exception("x"))
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    flush_langfuse()  # should not raise


def test_format_reasoning_inputs_as_markdown():
    rep = Representation()
    ts = datetime.datetime(2024, 5, 6, 7, 8, 9)
    md = format_reasoning_inputs_as_markdown(rep, "history text", "new turn", ts)
    assert "## Reasoning Inputs" in md
    assert "2024-05-06 07:08:09" in md
    assert "### Conversation History" in md
    assert "history text" in md
    assert "### New Turn" in md
    assert "new turn" in md


def test_format_reasoning_inputs_empty_history_turn():
    rep = Representation()
    ts = datetime.datetime(2024, 1, 1)
    md = format_reasoning_inputs_as_markdown(rep, "", "", ts)
    assert "### Conversation History" not in md
    assert "### New Turn" not in md


def test_accumulate_metric_appends(log_mod_clean):
    log_mod.accumulated_metrics.clear()
    accumulate_metric("task", "lat", 5.0, "ms")
    assert log_mod.accumulated_metrics["task"] == [("lat", 5.0, "ms")]


def test_accumulate_metric_evicts_oldest(log_mod_clean):
    log_mod.accumulated_metrics.clear()
    for i in range(log_mod.MAX_ACCUMULATED_TASKS):
        accumulate_metric(f"task{i}", "lat", i, "ms")
    # Now add a new task -> oldest evicted
    accumulate_metric("newtask", "lat", 0.0, "ms")
    assert "task0" not in log_mod.accumulated_metrics
    assert "newtask" in log_mod.accumulated_metrics


def test_log_token_usage_metrics(log_mod_clean):
    log_mod.accumulated_metrics.clear()
    log_token_usage_metrics("task", 100, 20, 30, 10)
    metrics = log_mod.accumulated_metrics["task"]
    by_label = {m[0]: m for m in metrics}
    assert by_label["input_tokens"] == ("input_tokens", 100, "tokens")
    assert by_label["output_tokens"] == ("output_tokens", 20, "tokens")
    assert by_label["uncached_input_tokens"] == (
        "uncached_input_tokens", 100 - 30 + 10, "tokens"
    )


def test_log_performance_metrics_noop(log_mod_clean):
    log_mod.accumulated_metrics.clear()
    log_performance_metrics("slug", "task", metrics=None)  # nothing accumulated


def test_log_performance_metrics_compact(monkeypatch, log_mod_clean):
    log_mod.accumulated_metrics.clear()
    monkeypatch.setattr(settings, "PERFORMANCE_LOG_FORMAT", "compact")
    monkeypatch.setattr(log_mod, "COLLECT_METRICS_LOCAL", False)
    accumulate_metric("slug_task", "lat", 100.0, "ms")
    log_performance_metrics("slug", "task")
    assert "slug_task" not in log_mod.accumulated_metrics


def test_log_performance_metrics_blob_at_debug(monkeypatch, caplog, log_mod_clean):
    log_mod.accumulated_metrics.clear()
    monkeypatch.setattr(settings, "PERFORMANCE_LOG_FORMAT", "compact")
    monkeypatch.setattr(log_mod, "COLLECT_METRICS_LOCAL", False)
    with caplog.at_level("DEBUG", logger="src.telemetry.logging"):
        log_performance_metrics("slug", "task", [("big", "BLOCK", "blob")])
    assert any("BLOCK" in r.message for r in caplog.records)


def test_log_performance_metrics_unknown_unit_and_blob_only(
    monkeypatch, caplog, log_mod_clean
):
    log_mod.accumulated_metrics.clear()
    monkeypatch.setattr(settings, "PERFORMANCE_LOG_FORMAT", "compact")
    monkeypatch.setattr(log_mod, "COLLECT_METRICS_LOCAL", False)
    # unknown unit hits the ``f"{value}{unit}"`` else branch; "s" and
    # "tokens" hit their dedicated formatting branches
    with caplog.at_level("DEBUG", logger="src.telemetry.logging"):
        log_performance_metrics(
            "slug", "task",
            [("size", 1024, "bytes"), ("dur", 2.0, "s"),
             ("tok", 5, "tokens")],
        )
    assert any("size=1024bytes" in r.message for r in caplog.records)
    assert any("dur=2.000s" in r.message for r in caplog.records)
    assert any("tok=5" in r.message for r in caplog.records)

    # only blobs -> summary_parts empty -> no-metrics logger.info branch
    log_mod.accumulated_metrics.clear()
    with caplog.at_level("INFO", logger="src.telemetry.logging"):
        log_performance_metrics("slug", "task2", [("big", "X", "blob")])
    assert any("PERFORMANCE slug_task2" in r.message for r in caplog.records)


def test_log_performance_metrics_collects_when_enabled(
    monkeypatch, log_mod_clean
):
    log_mod.accumulated_metrics.clear()
    monkeypatch.setattr(settings, "PERFORMANCE_LOG_FORMAT", "compact")
    monkeypatch.setattr(log_mod, "COLLECT_METRICS_LOCAL", True)
    called = {}
    monkeypatch.setattr(
        log_mod,
        "append_metrics_to_file",
        lambda slug, name, metrics: called.update(slug=slug, name=name, metrics=metrics),
    )
    log_performance_metrics("slug", "task", [("lat", 1.0, "ms")])
    assert called["slug"] == "slug"
    assert any(m[0] == "lat" for m in called["metrics"])


def test_log_performance_metrics_rich(monkeypatch, log_mod_clean):
    log_mod.accumulated_metrics.clear()
    monkeypatch.setattr(settings, "PERFORMANCE_LOG_FORMAT", "rich")
    monkeypatch.setattr(log_mod, "COLLECT_METRICS_LOCAL", False)
    log_performance_metrics("slug", "task", [("lat", 1.0, "ms"),
                                             ("tokens", 5, "tokens"),
                                             ("dur", 2.0, "s"),
                                             ("big", "blobval", "blob")])
    assert "slug_task" not in log_mod.accumulated_metrics


def test_normalize_template_path():
    assert normalize_template_path("/a/b/") == "/a/b"
    assert normalize_template_path("/a/b") == "/a/b"
    assert normalize_template_path("/") == "/"


def test_get_route_template_with_route():
    request = MagicMock()
    request.scope = {"route": MagicMock(path="/api/v1/websites/")}
    assert get_route_template(request) == "/api/v1/websites"


def test_get_route_template_unknown():
    request = MagicMock()
    request.scope = {"route": None}
    assert get_route_template(request) == "unknown"
