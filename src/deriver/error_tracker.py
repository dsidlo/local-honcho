"""In-memory error tracker for failed work units with exponential backoff.

Prevents infinite retry loops on transient failures while allowing self-healing.
Each QueueManager instance holds its own tracker; records are lost on process
restart (by design — transient errors that survive a restart should be retried
from scratch).
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.config import settings


@dataclass
class ErrorRecord:
    """Tracks retry state for a single work unit.

    ``escalate`` is decided atomically inside ``record_error`` rather than
    re-derived from the record afterwards, so the escalation decision cannot
    be flipped by another worker between the write and the read.

    ``retry_count`` is capped at ``max_retries`` (it is the retry budget that
    is left, not a raw tally); ``attempts`` is the uncapped number of failures
    observed for this record and is only for logging.
    """

    first_seen: datetime
    last_seen: datetime
    retry_count: int
    last_error: str
    backoff_until: datetime  # cooldown period
    escalate: bool = False  # retries exhausted: mark permanently errored
    attempts: int = 0  # uncapped failure tally, logging only


class WorkUnitErrorTracker:
    """Thread-safe in-memory tracker for failed work units.

    Keyed by work_unit_key. Provides exponential backoff so transient
    infrastructure failures (Ollama 500, network blips) can self-heal
    without hammering the system, while permanent errors eventually
    exhaust their retries and get marked as permanently failed in the DB.
    """

    def __init__(
        self,
        max_retries: int | None = None,
        base_backoff: float | None = None,
        max_backoff: float | None = None,
        ttl: float | None = None,
    ):
        # Default to the retry knobs on pi-dev's error tracker. Upstream moved
        # retry bookkeeping onto the queue item itself, so DERIVER.RETRY no
        # longer exists in settings; the tracker here now only serves the
        # standalone test suite and any opt-in re-use.
        retry_cfg = getattr(settings.DERIVER, "RETRY", None)
        self._max_retries: int = max_retries if max_retries is not None else getattr(retry_cfg, "MAX_RETRIES", 3)
        self._base_backoff: float = base_backoff if base_backoff is not None else getattr(retry_cfg, "BASE_BACKOFF_SEC", 5.0)
        self._max_backoff: float = max_backoff if max_backoff is not None else getattr(retry_cfg, "MAX_BACKOFF_SEC", 300.0)
        self._ttl: float = ttl if ttl is not None else getattr(retry_cfg, "ERROR_TRACKER_TTL_SEC", 3600.0)

        self._records: dict[str, ErrorRecord] = {}
        self._lock = threading.Lock()

    @property
    def max_retries(self) -> int:
        """Retry budget per work unit, for logging and tests."""
        return self._max_retries

    # ---- Public API ----

    def record_error(self, work_unit_key: str, error: str) -> ErrorRecord:
        """Record a failure. Returns updated record with backoff_until set.

        If the work unit was previously cleared (success then failure again),
        the retry count resets to 1.

        The escalation decision is made here, in the same lock-protected step
        as the write, and returned on the record. Once the retry budget is used
        up there is no cooldown left to wait for, so ``backoff_until`` is set to
        ``now`` instead of a future timestamp; otherwise the record would stay
        backed off forever and the permanent-failure branch could never run.
        """
        now = datetime.now(UTC)
        with self._lock:
            existing = self._records.get(work_unit_key)
            if existing is None:
                attempts = 1
                first_seen = now
            else:
                attempts = existing.attempts + 1
                first_seen = existing.first_seen

            retry_count = min(attempts, self._max_retries)
            escalate = retry_count >= self._max_retries
            backoff = 0.0 if escalate else self._calculate_backoff(retry_count)

            record = ErrorRecord(
                first_seen=first_seen,
                last_seen=now,
                retry_count=retry_count,
                last_error=error,
                backoff_until=now + timedelta(seconds=backoff),
                escalate=escalate,
                attempts=attempts,
            )
            self._records[work_unit_key] = record
            return record

    def is_backed_off(self, work_unit_key: str) -> bool:
        """True if the work unit is in cooldown (should not be re-claimed yet)."""
        with self._lock:
            record = self._records.get(work_unit_key)
            if record is None:
                return False
            return datetime.now(UTC) < record.backoff_until

    def is_exhausted(self, work_unit_key: str) -> bool:
        """True if retry_count >= max_retries (permanent failure)."""
        with self._lock:
            record = self._records.get(work_unit_key)
            if record is None:
                return False
            return record.retry_count >= self._max_retries

    def clear(self, work_unit_key: str) -> None:
        """Remove record on successful processing."""
        with self._lock:
            self._records.pop(work_unit_key, None)

    def should_escalate(self, work_unit_key: str) -> bool:
        """True if the record's atomic escalation decision is in effect.

        Thin accessor over ``record.escalate`` (computed in ``record_error``).
        The cooldown comparison is kept so a record that is still in backoff
        reads as "not yet", but for an exhausted record ``backoff_until`` is
        ``last_seen`` and this returns True immediately.

        Prefer the ``escalate`` field on the record returned by ``record_error``
        at call sites that just wrote the record: it avoids a second lock
        acquisition that another worker's ``clear()`` could race with.
        """
        with self._lock:
            record = self._records.get(work_unit_key)
            if record is None:
                return False
            return record.escalate and datetime.now(UTC) >= record.backoff_until

    def cleanup_expired(self) -> int:
        """Remove records older than TTL to prevent unbounded growth.

        Returns the number of records removed.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._ttl)
        with self._lock:
            expired_keys = [
                key
                for key, record in self._records.items()
                if record.last_seen < cutoff
            ]
            for key in expired_keys:
                del self._records[key]
        return len(expired_keys)

    def get_record(self, work_unit_key: str) -> ErrorRecord | None:
        """Return the current record for a work unit, or None."""
        with self._lock:
            return self._records.get(work_unit_key)

    # ---- Private ----

    def _calculate_backoff(self, retry_count: int) -> float:
        """Exponential backoff: base * 2^(retry_count - 1), capped at max_backoff."""
        backoff = self._base_backoff * (2 ** (retry_count - 1))
        return min(backoff, self._max_backoff)