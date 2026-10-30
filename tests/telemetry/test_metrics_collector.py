"""Unit tests for src/telemetry/metrics_collector.py."""

import json
from pathlib import Path

import pytest

from src.config import settings
from src.telemetry.metrics_collector import (
    MetricsCollector,
    append_metrics_to_file,
    get_metrics_file_path,
    load_metrics_from_file,
)


def _patch_metrics_file(monkeypatch, tmp_path):
    p = tmp_path / "metrics" / "metrics.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    monkeypatch.setattr(settings, "LOCAL_METRICS_FILE", str(p))
    return p


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------

def test_collector_init_defaults():
    c = MetricsCollector()
    assert c.run_id is None
    assert c.start_time is None
    assert c.metrics_by_type == {}
    assert c.task_count == 0
    assert c.is_collecting is False


def test_start_collection_resets_state():
    c = MetricsCollector()
    c.metrics_by_type["x_s"] = [1.0]
    c.start_collection("run-1")
    assert c.run_id == "run-1"
    assert c.is_collecting is True
    assert c.end_time is None
    assert c.metrics_by_type == {}


def test_collect_metrics_noop_when_not_collecting():
    c = MetricsCollector()
    c.collect_metrics([("lat", 1.0, "ms")])
    assert c.task_count == 0


def test_collect_metrics_normalizes_and_skips():
    c = MetricsCollector()
    c.start_collection("run-2")
    c.collect_metrics(
        [
            ("Latency MS", 12.5, "ms"),
            ("id", "abc", "id"),        # unit 'id' -> skipped
            ("tokens", "not-a-number", "tokens"),  # non-numeric -> skipped
            ("count", 3, "count"),
        ]
    )
    assert c.task_count == 1
    assert "latency_ms_ms" in c.metrics_by_type
    assert "id_id" not in c.metrics_by_type
    assert c.metrics_by_type["latency_ms_ms"] == [12.5]
    assert c.metrics_by_type["count_count"] == [3]


def test_collect_metrics_accumulates_multiple_tasks():
    c = MetricsCollector()
    c.start_collection("run-3")
    c.collect_metrics([("lat", 1.0, "ms")])
    c.collect_metrics([("lat", 2.0, "ms")])
    assert c.task_count == 2
    assert c.metrics_by_type["lat_ms"] == [1.0, 2.0]


def test_load_from_file_creates_file_if_missing(tmp_path, monkeypatch):
    p = tmp_path / "nested" / "metrics.jsonl"
    c = MetricsCollector()
    c.load_from_file(p)
    assert p.exists()
    assert p.read_text() == ""


def test_load_from_file_populates_collector(tmp_path, monkeypatch):
    p = tmp_path / "metrics.jsonl"
    p.write_text(
        json.dumps({"task_name": "t1", "metrics": [{"name": "lat",
                                                    "value": 5.0,
                                                    "unit": "ms"}]})
        + "\n"
    )
    c = MetricsCollector()
    c.start_collection("run-4")
    c.load_from_file(p)
    assert "lat_ms" in c.metrics_by_type
    assert c.metrics_by_type["lat_ms"] == [5.0]


def test_finalize_collection_loads_from_file(tmp_path, monkeypatch):
    _patch_metrics_file(monkeypatch, tmp_path).write_text(
        json.dumps({"task_name": "t1", "metrics": [{"name": "lat",
                                                    "value": 9.0,
                                                    "unit": "ms"}]})
        + "\n"
    )
    c = MetricsCollector()
    c.start_collection("run-5")
    c.finalize_collection()
    assert c.is_collecting is False
    assert c.end_time is not None
    assert c.metrics_by_type.get("lat_ms") == [9.0]


def test_finalize_collection_noop_when_not_collecting():
    c = MetricsCollector()
    c.finalize_collection()
    assert c.end_time is None


def test_get_aggregated_stats_basic():
    c = MetricsCollector()
    c.start_collection("run-6")
    c.collect_metrics([("lat", 10.0, "ms"), ("lat", 20.0, "ms"),
                       ("lat", 30.0, "ms")])
    stats = c.get_aggregated_stats()
    assert "lat" in stats
    s = stats["lat"]
    assert s["count"] == 3
    assert s["mean"] == 20.0
    assert s["median"] == 20.0
    assert s["min"] == 10.0
    assert s["max"] == 30.0
    assert abs(s["std_dev"] - 10.0) < 1e-9
    assert s["unit"] == "ms"
    assert s["raw_values"] == [10.0, 20.0, 30.0]


def test_get_aggregated_stats_single_value_std_zero():
    c = MetricsCollector()
    c.start_collection("run-7")
    c.collect_metrics([("lat", 5.0, "ms")])
    s = c.get_aggregated_stats()["lat"]
    assert s["std_dev"] == 0.0


def test_export_to_json(tmp_path):
    c = MetricsCollector()
    c.start_collection("run-8")
    c.collect_metrics([("Latency", 1.5, "ms")])
    c.finalize_collection()
    out = tmp_path / "export.json"
    c.export_to_json(out)
    data = json.loads(out.read_text())
    assert data["run_id"] == "run-8"
    assert data["total_tasks"] == 1
    assert "latency" in data["metrics_by_type"]
    assert "latency" in data["aggregated_stats"]


def test_export_to_json_raises_if_never_started(tmp_path):
    c = MetricsCollector()
    with pytest.raises(ValueError, match="never started"):
        c.export_to_json(tmp_path / "x.json")


def test_get_aggregated_stats_key_without_underscore():
    c = MetricsCollector()
    c.metrics_by_type["plain"] = [1.0, 2.0]
    stats = c.get_aggregated_stats()
    assert stats["plain"]["unit"] == "unknown"


def test_get_aggregated_stats_empty_values_skipped():
    c = MetricsCollector()
    c.metrics_by_type["empty_ms"] = []
    assert c.get_aggregated_stats() == {}


def test_print_summary_default_format_units(capsys):
    c = MetricsCollector()
    c.start_collection("run-11")
    c.collect_metrics([("count", 7, "count")])
    c.finalize_collection()
    c.print_summary()
    out = capsys.readouterr().out
    assert "count" in out
    assert "7" in out


def test_print_summary_empty(capsys):
    c = MetricsCollector()
    c.print_summary()
    assert "No metrics collected" in capsys.readouterr().out


def test_print_summary_populated(capsys, tmp_path):
    c = MetricsCollector()
    c.start_collection("run-9")
    c.collect_metrics([("lat", 1.0, "ms"), ("dur", 2.0, "s")])
    c.finalize_collection()
    c.print_summary()
    out = capsys.readouterr().out
    assert "PERFORMANCE METRICS SUMMARY" in out
    assert "lat" in out
    assert "dur" in out


def test_cleanup_collection_deletes_file(tmp_path, monkeypatch):
    p = _patch_metrics_file(monkeypatch, tmp_path)
    p.write_text("data\n")
    c = MetricsCollector()
    c.start_collection("run-10")
    c.cleanup_collection()
    assert c.is_collecting is False
    assert c.end_time is not None
    assert not p.exists()


# ---------------------------------------------------------------------------
# Module-level file helpers
# ---------------------------------------------------------------------------

def test_get_metrics_file_path_none(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_METRICS_FILE", None)
    assert get_metrics_file_path() is None


def test_get_metrics_file_path_value(monkeypatch, tmp_path):
    p = tmp_path / "m.jsonl"
    monkeypatch.setattr(settings, "LOCAL_METRICS_FILE", str(p))
    assert get_metrics_file_path() == Path(str(p))


def test_append_metrics_to_file_no_file(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_METRICS_FILE", None)
    append_metrics_to_file("slug", "task", [("lat", 1.0, "ms")])  # no-op


def test_append_metrics_to_file_writes(tmp_path, monkeypatch):
    p = _patch_metrics_file(monkeypatch, tmp_path)
    append_metrics_to_file("slug", "task", [("lat", 1.0, "ms")])
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_name"] == "slug_task"
    assert entry["metrics"][0]["name"] == "slug_lat"


def test_load_metrics_from_file_missing_returns_empty(tmp_path):
    assert load_metrics_from_file(tmp_path / "missing.jsonl") == []


def test_load_metrics_from_file_roundtrip(tmp_path, monkeypatch):
    p = _patch_metrics_file(monkeypatch, tmp_path)
    append_metrics_to_file("slug", "task", [("lat", 2.5, "ms")])
    loaded = load_metrics_from_file(p)
    assert len(loaded) == 1
    task_name, metrics_list = loaded[0]
    assert task_name == "slug_task"
    assert metrics_list == [("slug_lat", 2.5, "ms")]
