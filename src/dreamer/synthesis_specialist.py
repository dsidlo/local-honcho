"""
Synthesis Specialist for Honcho Dreamer.

Identifies high-value architectural improvements, especially state consolidation patterns.
Runs as Phase 3 of dream cycle (after deduction and induction).
"""

from typing import Any

from src.config import settings
from src.dreamer.specialists import BaseSpecialist


class SynthesisSpecialist(BaseSpecialist):
    """
    Creates synthesis observations from architectural analysis.

    Identifies:
    1. State consolidation opportunities (fragmented storage, distributed logic)
    2. Architecture gaps (missing patterns, incomplete implementations)
    3. Pattern synthesis (combining disjoint observations into novel approaches)
    4. Constraint challenges (alternative interpretations of assumptions)

    Only creates observations when high confidence and clear value exist.
    """

    name: str = "synthesis"
    peer_card_update_instruction: str = """
    Only update peer card for high-confidence, stable architectural preferences.
    Example: "PREFERENCE: Prefers centralized state management over distributed"
    Do NOT add temporary suggestions or implementation details.
    """

    def get_model(self) -> str:
        return getattr(settings.DREAM, "SYNTHESIS_MODEL", settings.DREAM.INDUCTION_MODEL)

    def get_max_tokens(self) -> int:
        return 8192

    def get_max_iterations(self) -> int:
        return 8  # Fewer iterations - quality over quantity

    def get_tools(self, *, peer_card_enabled: bool = True) -> list[dict[str, Any]]:
        # Same tools as induction but used differently
        from src.dreamer.specialists import (
            INDUCTION_SPECIALIST_TOOLS,
            PEER_CARD_TOOL_NAMES,
        )

        if peer_card_enabled:
            return INDUCTION_SPECIALIST_TOOLS
        return [
            t
            for t in INDUCTION_SPECIALIST_TOOLS
            if t["name"] not in PEER_CARD_TOOL_NAMES
        ]

    def build_system_prompt(
        self, observed: str, *, peer_card_enabled: bool = True
    ) -> str:
        peer_card_section = ""
        if peer_card_enabled:
            peer_card_section = """
## PEER CARD (REQUIRED - HIGH BAR)
Only update for stable architectural tendencies, not specific suggestions:
- GOOD: "PREFERENCE: Prefers state machines over flags"
- GOOD: "TRAIT: Values explicit state over implicit"
- BAD: "Should refactor X" (too specific)
- BAD: "Consider Y pattern" (suggestion, not trait)

Keep under 40 entries. Deduplicate."""

        return f"""You are a synthesis agent analyzing {observed}'s codebase and patterns.

## YOUR JOB
Identify high-value architectural improvements that merit attention.

## PHASE 1: DISCOVERY (Tool calls 1-3)
Gather context using available tools:
- `get_recent_observations` - Recent learnings across all levels
- `search_memory` - Find specific patterns
- `get_most_derived_observations` - Most reinforced patterns
- `get_peer_card` - Known tendencies

## PHASE 2: SYNTHESIS ANALYSIS (Tool calls 4-6)

Analyze for these patterns:

### 1. STATE CONSOLIDATION (Priority)
Scattered state management that could unify:

**A. Fragmented Storage**
- Same state type stored in 3+ locations
- Example: "user preferences" fetched in auth.py, api.py, and config.py

**B. Distributed Logic**  
- Same validation pattern in multiple places
- Example: "check status → validate → action" sequence repeated

**C. Coupling Opportunities**
- State change triggers same action in multiple contexts
- Example: Session invalidation handled in 4 different modules

**Threshold: Only report if pattern appears in 3+ locations**

### 2. ARCHITECTURE GAPS
- Intent described but not implemented
- Patterns started but not completed
- Missing error handling, validation, or tests

### 3. PATTERN SYNTHESIS
- Combining observation A + observation B suggests pattern C
- Example: "microservices" + "caching" = "caching service mesh"

### 4. CONSTRAINT CHALLENGES
- Assumptions that could be questioned
- Examples of synchronous patterns that could be async

## PHASE 3: CREATE OBSERVATIONS (Final calls)

Only create if ALL criteria met:
1. **Evidence-based**: Links to 2+ source observation IDs
2. **Specific**: Names files, functions, or line references
3. **Actionable**: Clear what to do, not vague advice
4. **Valuable**: Would save time or reduce errors
5. **Novel**: Not just restating existing observations

```json
{{
  "observations": [{{
    "content": "Clear description with specific references",
    "level": "synthesis",
    "source_ids": ["id1", "id2"],
    "synthesis_type": "state_consolidation|gap|pattern|challenge",
    "confidence": "low|medium|high",
    "rationale": "Why this matters"
  }}]
}}
```

## RULES
1. **MAX 3 OBSERVATIONS** - Quality over quantity
2. **NO GUESSING** - Must reference actual source_ids you found
3. **BE SPECIFIC** - "refactor auth.py lines 45-89" not "improve auth"
4. **STATE FOCUS** - Prioritize state consolidation patterns
5. **IF UNCERTAIN** - Skip rather than create weak observation
{peer_card_section}"""

    def build_user_prompt(
        self,
        hints: list[str] | None,
        peer_card: list[str] | None = None,
    ) -> str:
        peer_card_context = self._build_peer_card_context(peer_card)

        if hints:
            hints_str = "\n".join(f"- {q}" for q in hints[:5])
            return f"""{peer_card_context}
Analyze these areas and any related patterns:

{hints_str}

Start with `get_recent_observations` to see the full context, then focus on:
1. State management patterns (any scattered storage/logic?)
2. Repeated code sequences that could be unified
3. Gaps between intent and implementation

Create max 3 high-value synthesis observations."""

        return f"""{peer_card_context}
Explore {self.observed}'s learned patterns and identify synthesis opportunities.

Focus on:
1. **State consolidation** - Same logic in multiple places?
2. **Architecture gaps** - Intent not fully implemented?
3. **Pattern synthesis** - Novel combinations of existing patterns?

Start with `get_recent_observations` across all levels (explicit, deductive, inductive).
Then use semantic search for "state", "config", "validation", "duplicate", "refactor".

Only create observations with clear evidence and specific recommendations."""

    def filter_observations(
        self, observations: list[dict[str, Any]], min_sources: int = 2
    ) -> list[dict[str, Any]]:
        """
        Filter synthesis observations for quality.

        Only keeps observations that:
        - Have min_sources or more source_ids
        - Include specific references (file names, line numbers)
        - Provide clear value proposition
        """
        filtered = []
        for obs in observations:
            # Must have sufficient evidence
            sources = obs.get("source_ids", [])
            if len(sources) < min_sources:
                continue

            # Must have synthesis-specific fields
            if obs.get("level") != "synthesis":
                continue

            # Must have synthesis type
            if obs.get("synthesis_type") not in (
                "state_consolidation",
                "gap",
                "pattern",
                "challenge",
            ):
                continue

            filtered.append(obs)

        # Sort by confidence, return top 3
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        filtered.sort(
            key=lambda x: confidence_order.get(x.get("confidence", "low"), 2)
        )
        return filtered[:3]


# Singleton instance
SYNTHESIS_SPECIALIST = SynthesisSpecialist()
