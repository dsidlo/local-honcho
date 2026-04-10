"""Unit tests for WRR metrics module.

Tests that metrics functions correctly handle logging and
can optionally emit Prometheus-style metrics.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.deriver.wrr_metrics import (
    record_idle_fill,
    record_quota_calculation,
    record_starvation_prevention,
    record_task_type_query,
    record_wrr_poll_summary,
)


class TestRecordQuotaCalculation:
    """Test record_quota_calculation function."""

    def test_basic_logging(self):
        """Test that quota calculation is logged."""
        quotas = {"representation": 5, "webhook": 3, "idle_fill": 2}
        allocation = {"representation": 5, "webhook": 2, "idle_fill": 2}

        with patch("src.deriver.wrr_metrics.logger") as mock_logger:
            record_quota_calculation(quotas, allocation)
            mock_logger.debug.assert_called_once()

    def test_empty_dicts(self):
        """Test with empty input."""
        record_quota_calculation({}, {})
        # Should not raise


class TestRecordTaskTypeQuery:
    """Test record_task_type_query function."""

    def test_basic_logging(self):
        """Test that task type query is logged."""
        with patch("src.deriver.wrr_metrics.logger") as mock_logger:
            record_task_type_query("representation", 10, 8)
            mock_logger.debug.assert_called_once()

    def test_starvation_warning(self):
        """Test warning logged when nothing returned."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_metrics:
            mock_metrics.return_value = True
            with patch("src.deriver.wrr_metrics.logger") as mock_logger:
                record_task_type_query("representation", 10, 0)
                mock_logger.warning.assert_called_once()

    def test_no_warning_when_results(self):
        """Test no warning when results returned."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_metrics:
            mock_metrics.return_value = True
            with patch("src.deriver.wrr_metrics.logger") as mock_logger:
                record_task_type_query("representation", 10, 10)
                mock_logger.warning.assert_not_called()


class TestRecordIdleFill:
    """Test record_idle_fill function."""

    def test_efficiency_calculation(self):
        """Test efficiency is calculated correctly."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_metrics:
            mock_metrics.return_value = True
            with patch("src.deriver.wrr_metrics.logger") as mock_logger:
                record_idle_fill(filled_count=5, requested_count=10, strategy="oldest_first")
                # Check that efficiency=50% was logged
                log_call = mock_logger.debug.call_args[0][0]
                assert "50.00%" in log_call

    def test_full_efficiency_no_info(self):
        """Test no info logged at 100% efficiency."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_metrics:
            mock_metrics.return_value = True
            with patch("src.deriver.wrr_metrics.logger") as mock_logger:
                mock_logger.info = MagicMock()
                record_idle_fill(filled_count=10, requested_count=10, strategy="oldest_first")
                # At 100%, the info log shouldn't fire
                mock_logger.info.assert_not_called()

    def test_partial_efficiency_info_logged(self):
        """Test info logged when fill is partial."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_metrics:
            mock_metrics.return_value = True
            with patch("src.deriver.wrr_metrics.logger") as mock_logger:
                record_idle_fill(filled_count=5, requested_count=10, strategy="oldest_first")
                mock_logger.info.assert_called_once()


class TestRecordStarvationPrevention:
    """Test record_starvation_prevention function."""

    def test_always_logs_warning(self):
        """Test that starvation prevention always logs warning."""
        with patch("src.deriver.wrr_metrics.logger") as mock_logger:
            record_starvation_prevention("representation", "token_threshold_met")
            mock_logger.warning.assert_called_once()

    def test_includes_details(self):
        """Test that details are included in log."""
        with patch("src.deriver.wrr_metrics.logger") as mock_logger:
            record_starvation_prevention(
                "representation",
                "age_threshold_met",
                {"wait_time": 3600}
            )
            log_call = mock_logger.warning.call_args[0][0]
            assert "wait_time" in log_call


class TestRecordWrrPollSummary:
    """Test record_wrr_poll_summary function."""

    def test_complete_poll_cycle(self):
        """Test recording a complete poll cycle."""
        quotas = {"representation": 5, "webhook": 3}
        allocations = {"representation": 5, "webhook": 2}

        record_wrr_poll_summary(
            quotas=quotas,
            allocations=allocations,
            idle_fill_requested=2,
            idle_fill_filled=2,
            strategy="oldest_first",
        )
        # Should not raise

    def test_zero_idle_fill(self):
        """Test when idle_fill is not used."""
        quotas = {"representation": 5, "webhook": 5}
        allocations = {"representation": 5, "webhook": 5}

        record_wrr_poll_summary(
            quotas=quotas,
            allocations=allocations,
            idle_fill_requested=0,
            idle_fill_filled=0,
            strategy="oldest_first",
        )
        # Should not raise


class TestMetricsDisabled:
    """Test behavior when metrics are disabled."""

    def test_quota_calculation_no_metrics(self):
        """Test quota calculation works when metrics disabled."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_check:
            mock_check.return_value = False
            record_quota_calculation({"a": 1}, {"a": 1})
            # Should only log, not record metrics

    def test_task_query_no_metrics(self):
        """Test task query works when metrics disabled."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_check:
            mock_check.return_value = False
            record_task_type_query("a", 5, 5)
            # Should only log, not record metrics

    def test_idle_fill_no_metrics(self):
        """Test idle fill works when metrics disabled."""
        with patch("src.deriver.wrr_metrics._should_record_metrics") as mock_check:
            mock_check.return_value = False
            record_idle_fill(5, 10, "oldest_first")
            # Should only log, not record metrics
