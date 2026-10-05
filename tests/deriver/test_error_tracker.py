"""Tests for WorkUnitErrorTracker and queue item retry logic."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.deriver.error_tracker import ErrorRecord, WorkUnitErrorTracker


class TestErrorRecord:
    """Unit tests for the ErrorRecord dataclass."""

    def test_creation(self):
        now = datetime.now(timezone.utc)
        record = ErrorRecord(
            first_seen=now,
            last_seen=now,
            retry_count=1,
            last_error="test error",
            backoff_until=now + timedelta(seconds=5),
        )
        assert record.retry_count == 1
        assert record.last_error == "test error"


class TestWorkUnitErrorTracker:
    """Unit tests for the WorkUnitErrorTracker."""

    def test_record_first_error(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)
        record = tracker.record_error("wu:1", "Ollama 500")

        assert record.retry_count == 1
        assert record.last_error == "Ollama 500"
        assert record.backoff_until > datetime.now(timezone.utc)

    def test_record_increments_retry_count(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error 1")
        record = tracker.record_error("wu:1", "error 2")

        assert record.retry_count == 2
        assert record.last_error == "error 2"

    def test_is_backed_off_during_backoff(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=10.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error")

        # Should be backed off immediately after recording
        assert tracker.is_backed_off("wu:1") is True

    def test_is_not_backed_off_for_unknown_key(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)
        assert tracker.is_backed_off("unknown") is False

    def test_is_not_backed_off_after_backoff_expires(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=0.01, max_backoff=0.1, ttl=3600.0)

        tracker.record_error("wu:1", "error")
        time.sleep(0.05)  # Wait for backoff to expire

        assert tracker.is_backed_off("wu:1") is False

    def test_is_exhausted_after_max_retries(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error 1")
        tracker.record_error("wu:1", "error 2")
        assert tracker.is_exhausted("wu:1") is False

        tracker.record_error("wu:1", "error 3")
        assert tracker.is_exhausted("wu:1") is True

    def test_is_not_exhausted_for_unknown_key(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)
        assert tracker.is_exhausted("unknown") is False

    def test_clear_removes_record(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error")
        assert tracker.is_backed_off("wu:1") is True

        tracker.clear("wu:1")
        assert tracker.is_backed_off("wu:1") is False
        assert tracker.is_exhausted("wu:1") is False

    def test_clear_nonexistent_key_no_error(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)
        tracker.clear("nonexistent")  # Should not raise

    def test_should_escalate_immediately_when_exhausted(self):
        """Escalation is reachable on the production path: no sleep, no cooldown left.

        The old shape of this test asserted the opposite (escalation only after
        the backoff expired). That is the state production never reaches: the
        call site reads the record immediately after writing it, and the record
        carried a strictly-future cooldown. With the fix, an exhausted record
        sets ``backoff_until == last_seen`` so the decision is true right away.
        """
        tracker = WorkUnitErrorTracker(max_retries=2, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error 1")
        record = tracker.record_error("wu:1", "error 2")

        # Retries used up -> escalate immediately, no sleep in between.
        assert tracker.should_escalate("wu:1") is True
        assert record.escalate is True
        assert record.backoff_until == record.last_seen

    def test_escalation_decision_on_returned_record(self):
        """The record returned by ``record_error`` carries the decision atomically."""
        tracker = WorkUnitErrorTracker(max_retries=2, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        first = tracker.record_error("wu:1", "error 1")
        assert first.escalate is False
        assert first.backoff_until > first.last_seen

        second = tracker.record_error("wu:1", "error 2")
        assert second.escalate is True

    def test_should_not_escalate_with_retries_remaining(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=0.01, max_backoff=0.1, ttl=3600.0)

        tracker.record_error("wu:1", "error 1")
        time.sleep(0.05)

        # Retries remain, should not escalate
        assert tracker.should_escalate("wu:1") is False

    def test_exponential_backoff_schedule(self):
        tracker = WorkUnitErrorTracker(max_retries=5, base_backoff=1.0, max_backoff=300.0, ttl=3600.0)

        record1 = tracker.record_error("wu:1", "error")
        backoff1 = (record1.backoff_until - record1.last_seen).total_seconds()
        assert 0.9 <= backoff1 <= 1.1  # ~1.0s

        record2 = tracker.record_error("wu:1", "error")
        backoff2 = (record2.backoff_until - record2.last_seen).total_seconds()
        assert 1.9 <= backoff2 <= 2.1  # ~2.0s

        record3 = tracker.record_error("wu:1", "error")
        backoff3 = (record3.backoff_until - record3.last_seen).total_seconds()
        assert 3.9 <= backoff3 <= 4.1  # ~4.0s

        record4 = tracker.record_error("wu:1", "error")
        backoff4 = (record4.backoff_until - record4.last_seen).total_seconds()
        assert 7.9 <= backoff4 <= 8.1  # ~8.0s

    def test_backoff_capped_at_max(self):
        tracker = WorkUnitErrorTracker(max_retries=10, base_backoff=100.0, max_backoff=300.0, ttl=3600.0)

        record = tracker.record_error("wu:1", "error")
        backoff = (record.backoff_until - record.last_seen).total_seconds()
        assert backoff <= 300.0  # Capped at max_backoff

    def test_cleanup_expired_removes_old_records(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=0.01)

        tracker.record_error("wu:1", "error")
        time.sleep(0.02)  # Wait for TTL to expire

        removed = tracker.cleanup_expired()
        assert removed == 1
        assert tracker.get_record("wu:1") is None

    def test_cleanup_expired_keeps_fresh_records(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error")
        removed = tracker.cleanup_expired()
        assert removed == 0
        assert tracker.get_record("wu:1") is not None

    def test_get_record_returns_none_for_unknown(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)
        assert tracker.get_record("unknown") is None

    def test_clear_then_record_resets_retry_count(self):
        """After a successful clear, a new failure should start from retry 1."""
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error 1")
        tracker.record_error("wu:1", "error 2")
        assert tracker.get_record("wu:1").retry_count == 2

        tracker.clear("wu:1")
        tracker.record_error("wu:1", "error 3")

        record = tracker.get_record("wu:1")
        assert record.retry_count == 1  # Reset after clear

    def test_different_work_units_tracked_independently(self):
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        tracker.record_error("wu:1", "error A")
        tracker.record_error("wu:2", "error B")

        assert tracker.get_record("wu:1").retry_count == 1
        assert tracker.get_record("wu:2").retry_count == 1
        assert tracker.get_record("wu:1").last_error == "error A"
        assert tracker.get_record("wu:2").last_error == "error B"

    def test_retry_count_bounded_by_max_retries(self):
        """``retry_count`` stops at the cap; ``attempts`` keeps counting for logging."""
        tracker = WorkUnitErrorTracker(max_retries=3, base_backoff=1.0, max_backoff=60.0, ttl=3600.0)

        for expected_attempts in range(1, 6):
            record = tracker.record_error("wu:1", f"error {expected_attempts}")
            assert record.attempts == expected_attempts
            assert record.retry_count == min(expected_attempts, 3)

        # Past the cap the record stays exhausted and never re-arms a cooldown.
        assert tracker.is_exhausted("wu:1") is True
        assert tracker.should_escalate("wu:1") is True
        assert tracker.is_backed_off("wu:1") is False

    def test_uses_settings_defaults(self):
        """Tracker falls back to built-in defaults when DERIVER.RETRY is absent.

        Upstream moved retry bookkeeping onto the queue item payload, so the
        pi-dev DeriverRetrySettings block no longer exists on the merged tree;
        the tracker stays usable standalone with its own defaults.
        """
        tracker = WorkUnitErrorTracker()
        assert tracker._max_retries == 3
        assert tracker._base_backoff == 5.0