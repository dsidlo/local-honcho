"""Unit tests for ``src/dreamer/synthesis_specialist.py``."""

from __future__ import annotations

import pytest

from src.config import settings
from src.dreamer.specialists import (
    INDUCTION_SPECIALIST_TOOLS,
    PEER_CARD_TOOL_NAMES,
)
from src.dreamer.synthesis_specialist import (
    SYNTHESIS_SPECIALIST,
    SynthesisSpecialist,
)
from src.exceptions import ValidationException

SPECIALIST = SYNTHESIS_SPECIALIST


class TestModelConfig:
    def test_get_model_config_uses_induction_config(self):
        assert SPECIALIST.get_model_config() is settings.DREAM.INDUCTION_MODEL_CONFIG

    def test_get_model_config_raises_when_unresolved(self, monkeypatch):
        monkeypatch.setattr(settings.DREAM, "INDUCTION_MODEL_CONFIG", None)
        with pytest.raises(ValidationException, match="DREAM SYNTHESIS"):
            SPECIALIST.get_model_config()

    def test_get_max_tokens(self):
        assert SPECIALIST.get_max_tokens() == 8192

    def test_get_max_iterations(self):
        assert SPECIALIST.get_max_iterations() == 8


class TestTools:
    def test_get_tools_enabled_returns_induction_tools(self):
        tools = SPECIALIST.get_tools(peer_card_enabled=True)
        assert tools == INDUCTION_SPECIALIST_TOOLS

    def test_get_tools_disabled_excludes_peer_card(self):
        tools = SPECIALIST.get_tools(peer_card_enabled=False)
        assert tools == [
            t
            for t in INDUCTION_SPECIALIST_TOOLS
            if t["name"] not in PEER_CARD_TOOL_NAMES
        ]
        assert "update_peer_card" not in {t["name"] for t in tools}


class TestPrompts:
    def test_build_system_prompt_includes_peer_card_section(self):
        prompt = SPECIALIST.build_system_prompt("alice", peer_card_enabled=True)
        assert "alice" in prompt
        assert "PEER CARD (REQUIRED - HIGH BAR)" in prompt
        assert "PREFERENCE: Prefers state machines over flags" in prompt

    def test_build_system_prompt_without_peer_card_section(self):
        prompt = SPECIALIST.build_system_prompt("alice", peer_card_enabled=False)
        assert "alice" in prompt
        assert "HIGH BAR" not in prompt
        assert "PEER CARD (REQUIRED" not in prompt

    def test_build_user_prompt_with_hints_and_peer_card(self):
        hints = [f"hint {i}" for i in range(7)]
        peer_card = ["IDENTITY: Name: Zorblax-Prime"]
        prompt = SPECIALIST.build_user_prompt(
            "alice", hints=hints, peer_card=peer_card
        )
        assert "CURRENT PEER CARD" in prompt
        assert "IDENTITY: Name: Zorblax-Prime" in prompt
        # Only the first 5 hints are included.
        assert "- hint 0" in prompt
        assert "- hint 4" in prompt
        assert "- hint 5" not in prompt
        assert "Start with `get_recent_observations`" in prompt

    def test_build_user_prompt_without_hints_or_peer_card(self):
        prompt = SPECIALIST.build_user_prompt("alice", hints=None, peer_card=None)
        assert "CURRENT PEER CARD" not in prompt
        assert "Explore alice's learned patterns" in prompt
        assert "state consolidation" in prompt.lower()


class TestFilterObservations:
    def _observations(self) -> list[dict[str, object]]:
        return [
            # Too few sources -> dropped.
            {
                "source_ids": ["a"],
                "level": "synthesis",
                "synthesis_type": "gap",
                "confidence": "high",
            },
            # Wrong level -> dropped.
            {
                "source_ids": ["a", "b"],
                "level": "explicit",
                "synthesis_type": "gap",
                "confidence": "high",
            },
            # Unknown synthesis type -> dropped.
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "nonsense",
                "confidence": "high",
            },
            # Valid medium.
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "gap",
                "confidence": "medium",
            },
            # Valid high.
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "pattern",
                "confidence": "high",
            },
            # Valid low.
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "challenge",
                "confidence": "low",
            },
            # Valid but missing confidence -> treated as low.
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "state_consolidation",
            },
        ]

    def test_filter_keeps_quality_observations_sorted_by_confidence(self):
        filtered = SPECIALIST.filter_observations(self._observations())
        assert [o["synthesis_type"] for o in filtered] == [
            "pattern",
            "gap",
            "challenge",
        ]
        assert [o["confidence"] for o in filtered] == ["high", "medium", "low"]

    def test_filter_caps_at_three(self):
        observations = [
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "gap",
                "confidence": "high",
            }
            for _ in range(5)
        ]
        filtered = SPECIALIST.filter_observations(observations)
        assert len(filtered) == 3

    def test_filter_honors_min_sources(self):
        observations = [
            {
                "source_ids": ["a", "b"],
                "level": "synthesis",
                "synthesis_type": "gap",
                "confidence": "high",
            },
            {
                "source_ids": ["a", "b", "c"],
                "level": "synthesis",
                "synthesis_type": "gap",
                "confidence": "high",
            },
        ]
        filtered = SPECIALIST.filter_observations(observations, min_sources=3)
        assert len(filtered) == 1
        assert len(filtered[0]["source_ids"]) == 3

    def test_filter_empty_input(self):
        assert SPECIALIST.filter_observations([]) == []

    def test_singleton_is_synthesis(self):
        assert SYNTHESIS_SPECIALIST.name == "synthesis"
        assert isinstance(SYNTHESIS_SPECIALIST, SynthesisSpecialist)
