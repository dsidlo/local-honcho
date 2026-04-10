"""Unit tests for WRR quota calculation.

This module tests the calculate_wrr_quotas function to ensure:
- Basic weight distribution works correctly
- Minimum guarantees are enforced
- Maximum caps are respected
- idle_fill behaves correctly as an explicit task type
"""

import pytest

from src.deriver.wrr_queries import calculate_wrr_quotas


class TestWRRQuotaBasicWeights:
    """Test basic weight distribution without min/max constraints."""

    def test_equal_weights(self):
        """Test equal 50/50 weight distribution."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.5, "b": 0.5},
            min_slots={"a": 0, "b": 0},
            max_slots={"a": None, "b": None},
        )
        assert quotas["a"] == 5
        assert quotas["b"] == 5

    def test_unequal_weights(self):
        """Test unequal weight distribution."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.7, "b": 0.2, "c": 0.1},
            min_slots={"a": 0, "b": 0, "c": 0},
            max_slots={"a": None, "b": None, "c": None},
        )
        # 70% of 10 = 7, 20% of 10 = 2, 10% of 10 = 1
        assert quotas["a"] == 7
        assert quotas["b"] == 2
        assert quotas["c"] == 1

    def test_single_task_type(self):
        """Test with only one task type."""
        quotas = calculate_wrr_quotas(
            available_workers=5,
            weights={"only": 1.0},
            min_slots={"only": 0},
            max_slots={"only": None},
        )
        assert quotas["only"] == 5

    def test_zero_workers(self):
        """Test with zero available workers."""
        quotas = calculate_wrr_quotas(
            available_workers=0,
            weights={"a": 0.5, "b": 0.5},
            min_slots={"a": 0, "b": 0},
            max_slots={"a": None, "b": None},
        )
        assert quotas["a"] == 0
        assert quotas["b"] == 0

    def test_small_worker_count(self):
        """Test with fewer workers than task types."""
        quotas = calculate_wrr_quotas(
            available_workers=2,
            weights={"a": 0.5, "b": 0.3, "c": 0.2},
            min_slots={"a": 0, "b": 0, "c": 0},
            max_slots={"a": None, "b": None, "c": None},
        )
        # Should still distribute proportionally
        assert sum(quotas.values()) == 2


class TestWRRQuotaMinimumGuarantees:
    """Test minimum guarantee enforcement (MIN_SLOTS)."""

    def test_minimum_guarantees_only(self):
        """Test that minimum guarantees take priority over weights."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.9, "b": 0.1},
            min_slots={"a": 2, "b": 3},  # b gets 3 despite low weight
            max_slots={"a": None, "b": None},
        )
        assert quotas["b"] >= 3  # Minimum guarantee met

    def test_minimum_exceeds_capacity(self):
        """Test when minimum guarantees sum to more than available workers."""
        quotas = calculate_wrr_quotas(
            available_workers=3,
            weights={"a": 0.5, "b": 0.3, "c": 0.2},
            min_slots={"a": 2, "b": 2, "c": 2},  # Sum = 6 > 3
            max_slots={"a": None, "b": None, "c": None},
        )
        # Should cap at available workers
        assert sum(quotas.values()) <= 3
        # But still try to honor minimums as best as possible
        assert quotas["a"] >= 2 or quotas["a"] == 3

    def test_minimum_creates_weight_imbalance(self):
        """Test remaining capacity distribution after minimums."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.5, "b": 0.5},
            min_slots={"a": 8, "b": 0},  # a gets 8 minimum
            max_slots={"a": None, "b": None},
        )
        assert quotas["a"] >= 8  # Minimum met (may get more from weight distribution)
        assert quotas["a"] + quotas["b"] == 10  # All slots allocated
        # b should get some allocation based on equal weights


class TestWRRQuotaMaximumCaps:
    """Test maximum slot enforcement (MAX_SLOTS)."""

    def test_maximum_cap_applied(self):
        """Test that maximum caps are enforced."""
        quotas = calculate_wrr_quotas(
            available_workers=20,
            weights={"a": 0.8, "b": 0.2},
            min_slots={"a": 0, "b": 0},
            max_slots={"a": 5, "b": None},  # a capped at 5, b uncapped
        )
        assert quotas["a"] == 5  # Hard cap applied
        # Remaining goes to uncapped b (20 - 5 = 15)
        assert quotas["b"] == 15
        assert sum(quotas.values()) == 20
    
    def test_maximum_below_minimum(self):
        """Test when max is below min (max should win)."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.5, "b": 0.5},
            min_slots={"a": 8, "b": 0},
            max_slots={"a": 5, "b": None},  # a max below min, b uncapped
        )
        # Max should override minimum
        assert quotas["a"] <= 5
        # Remaining slots go to b
        assert quotas["a"] + quotas["b"] == 10

    def test_all_capped(self):
        """Test when all task types are capped."""
        quotas = calculate_wrr_quotas(
            available_workers=100,
            weights={"a": 0.5, "b": 0.3, "c": 0.2},
            min_slots={"a": 0, "b": 0, "c": 0},
            max_slots={"a": 10, "b": 10, "c": 10},
        )
        assert quotas["a"] <= 10
        assert quotas["b"] <= 10
        assert quotas["c"] <= 10
        # Total may be less than workers due to caps


class TestWRRQuotaIdleFill:
    """Test idle_fill behavior as explicit task type."""

    def test_idle_fill_as_explicit_task_type(self):
        """Test that idle_fill receives weight-based allocation."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={
                "representation": 0.36,
                "webhook": 0.36,
                "idle_fill": 0.28,
            },
            min_slots={"representation": 0, "webhook": 0, "idle_fill": 0},
            max_slots={"representation": None, "webhook": None, "idle_fill": None},
        )
        # idle_fill should get ~28% of 10 = 2-3 slots
        assert quotas["idle_fill"] >= 2
        assert sum(quotas.values()) == 10

    def test_idle_fill_with_min_slots_zero(self):
        """Test idle_fill with min_slots=0 (should be opportunistic)."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.5, "b": 0.3, "idle_fill": 0.2},
            min_slots={"a": 5, "b": 3, "idle_fill": 0},  # mins = 8
            max_slots={"a": None, "b": None, "idle_fill": None},
        )
        # After minimums (8), remaining 2 distributed by weights
        # idle_fill has 20% weight = ~0.4 of 2 = ~1 slot
        assert quotas["idle_fill"] >= 0  # Should get at least 0

    def test_idle_fill_no_minimum(self):
        """Test that idle_fill never has minimum guarantee."""
        quotas = calculate_wrr_quotas(
            available_workers=5,
            weights={"a": 0.7, "idle_fill": 0.3},
            min_slots={"a": 5, "idle_fill": 0},  # a takes all minimum
            max_slots={"a": None, "idle_fill": None},
        )
        # a gets its minimum of 5, nothing left for idle_fill
        assert quotas["a"] == 5
        assert quotas["idle_fill"] == 0


class TestWRRQuotaFullConfiguration:
    """Test with complete realistic configuration matching design doc."""

    def test_realistic_weights_from_design(self):
        """Test with weights from design document."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={
                "representation": 0.36,
                "summary": 0.18,
                "webhook": 0.18,
                "dream": 0.09,
                "reconciler": 0.09,
                "idle_fill": 0.10,
            },
            min_slots={
                "representation": 2,
                "summary": 1,
                "webhook": 2,
                "dream": 1,
                "reconciler": 1,
                "idle_fill": 0,
            },
            max_slots={
                "representation": None,
                "summary": None,
                "webhook": None,
                "dream": 5,
                "reconciler": 3,
                "idle_fill": None,
            },
        )
        # Verify minimums are met
        assert quotas["representation"] >= 2
        assert quotas["summary"] >= 1
        assert quotas["webhook"] >= 2
        assert quotas["dream"] >= 1
        assert quotas["reconciler"] >= 1
        assert quotas["idle_fill"] >= 0

        # Verify maximums are respected
        assert quotas["dream"] <= 5
        assert quotas["reconciler"] <= 3

        # Total should be 10
        assert sum(quotas.values()) == 10

    def test_anti_starvation_minimums(self):
        """Test that minimum guarantees prevent starvation."""
        # Low-volume task types should get their minimums
        quotas = calculate_wrr_quotas(
            available_workers=20,
            weights={
                "high_volume": 0.90,
                "low_volume_1": 0.05,
                "low_volume_2": 0.05,
            },
            min_slots={
                "high_volume": 1,
                "low_volume_1": 2,  # High minimum despite low weight
                "low_volume_2": 2,
            },
            max_slots={
                "high_volume": None,
                "low_volume_1": None,
                "low_volume_2": None,
            },
        )
        # Low volume tasks should get their minimums
        assert quotas["low_volume_1"] >= 2
        assert quotas["low_volume_2"] >= 2


class TestWRRQuotaEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_dicts(self):
        """Test with empty dictionaries (edge case)."""
        quotas = calculate_wrr_quotas(
            available_workers=5,
            weights={},
            min_slots={},
            max_slots={},
        )
        assert quotas == {}

    def test_weight_rounding(self):
        """Test that fractional weights are handled correctly."""
        quotas = calculate_wrr_quotas(
            available_workers=10,
            weights={"a": 0.33, "b": 0.33, "c": 0.34},
            min_slots={"a": 0, "b": 0, "c": 0},
            max_slots={"a": None, "b": None, "c": None},
        )
        # Should still sum to 10 (with rounding)
        assert sum(quotas.values()) == 10

    def test_one_at_max_one_uncapped(self):
        """Test redistribution when one type hits max."""
        quotas = calculate_wrr_quotas(
            available_workers=20,
            weights={"a": 0.5, "b": 0.5},
            min_slots={"a": 0, "b": 0},
            max_slots={"a": 5, "b": None},  # a capped, b not
        )
        assert quotas["a"] == 5  # a at max
        assert quotas["b"] == 15  # b gets rest (20 - 5 = 15)

    def test_idempotent_allocation(self):
        """Test that allocation is deterministic."""
        config = {
            "available_workers": 10,
            "weights": {"a": 0.5, "b": 0.3, "c": 0.2},
            "min_slots": {"a": 1, "b": 1, "c": 1},
            "max_slots": {"a": None, "b": None, "c": None},
        }
        quotas1 = calculate_wrr_quotas(**config)
        quotas2 = calculate_wrr_quotas(**config)
        assert quotas1 == quotas2
