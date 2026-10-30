"""Unit tests for src/telemetry/reasoning_traces.py."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.config import settings
from src.telemetry.reasoning_traces import (
    get_reasoning_traces_file_path,
    log_reasoning_trace,
)


def _fake_response(**overrides):
    defaults = dict(
        content="hello",
        input_tokens=10,
        output_tokens=5,
        finish_reasons=["stop"],
        thinking_content=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_model_config(transport="openai", model="gpt-4o-mini"):
    return SimpleNamespace(transport=transport, model=model)


class _PydanticContent(BaseModel):
    text: str = "payload"


def test_get_reasoning_traces_file_path_none(monkeypatch):
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", None)
    assert get_reasoning_traces_file_path() is None


def test_get_reasoning_traces_file_path_value(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", str(tmp_path / "x.jsonl"))
    assert get_reasoning_traces_file_path() == tmp_path / "x.jsonl"


def test_log_reasoning_trace_noop_when_no_file(monkeypatch):
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", None)
    log_reasoning_trace("t", _fake_model_config(), "p", _fake_response())


def test_log_reasoning_trace_writes_prompt_form(monkeypatch, tmp_path):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", str(p))
    log_reasoning_trace(
        "minimal_deriver",
        _fake_model_config(),
        "the prompt",
        _fake_response(),
        max_tokens=100,
        thinking_budget_tokens=200,
        reasoning_effort="medium",
        json_mode=True,
        stop_seqs=["<stop>"],
    )
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_type"] == "minimal_deriver"
    assert entry["provider"] == "openai"
    assert entry["model"] == "gpt-4o-mini"
    assert entry["settings"]["max_tokens"] == 100
    assert entry["settings"]["thinking_budget_tokens"] == 200
    assert entry["settings"]["reasoning_effort"] == "medium"
    assert entry["settings"]["json_mode"] is True
    assert entry["settings"]["stop_seqs"] == ["<stop>"]
    assert entry["input"]["tokens"] == 10
    assert entry["input"]["prompt"] == "the prompt"
    assert entry["output"]["content"] == "hello"
    assert entry["output"]["finish_reasons"] == ["stop"]


def test_log_reasoning_trace_uses_messages(monkeypatch, tmp_path):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", str(p))
    log_reasoning_trace(
        "agentic",
        _fake_model_config(transport="anthropic", model="claude"),
        "prompt-ignored",
        _fake_response(),
        messages=[{"role": "user", "content": "hi"}],
    )
    entry = json.loads(p.read_text().strip().splitlines()[0])
    assert "messages" in entry["input"]
    assert "prompt" not in entry["input"]


def test_log_reasoning_trace_serializes_pydantic_content(monkeypatch, tmp_path):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", str(p))
    log_reasoning_trace(
        "t", _fake_model_config(), "p", _fake_response(content=_PydanticContent())
    )
    entry = json.loads(p.read_text().strip().splitlines()[0])
    assert entry["output"]["content"] == {"text": "payload"}


def test_log_reasoning_trace_includes_tool_calls(monkeypatch, tmp_path):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", str(p))
    resp = _fake_response(tool_calls_made=[{"name": "search_memory"}])
    log_reasoning_trace("t", _fake_model_config(), "p", resp)
    entry = json.loads(p.read_text().strip().splitlines()[0])
    assert entry["output"]["tool_calls"] == [{"name": "search_memory"}]


def test_log_reasoning_trace_no_tool_calls_attr(monkeypatch, tmp_path):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setattr(settings, "REASONING_TRACES_FILE", str(p))
    log_reasoning_trace("t", _fake_model_config(), "p", _fake_response())
    entry = json.loads(p.read_text().strip().splitlines()[0])
    assert "tool_calls" not in entry["output"]
