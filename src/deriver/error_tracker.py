"""In-memory error tracker for failed work units with exponential backoff.

Prevents infinite retry loops on transient failures while allowing self-healing.
Each QueueManager instance holds its own tracker; records are lost on process
restart (by design — transient errors that survive a restart should be retried
from scratch).
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.config import settings


@dataclass
class ErrorRecord:
    """Tracks retry state for a single work unit."""

    first_seen: datetime
    last_seen: datetime
    retry_count: int
    last_error: str
    backoff_until: datetime  # cooldown period


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
        cfg = settings.DERIVER.RETRY
        self._max_retries: int = max_retries if max_retries is not None else cfg.MAX_RETRIES
        self._base_backoff: float = base_backoff if base_backoff is not None else cfg.BASE_BACKOFF_SEC
        self._max_backoff: float = max_backoff if max_backoff is not None else cfg.MAX_BACKOFF_SEC
        self._ttl: float = ttl if ttl is not None else cfg.ERROR_TRACKER_TTL_SEC

        self._records: dict[str, ErrorRecord] = {}
        self._lock = threading.Lock()

    # ---- Public API ----

    def record_error(self, work_unit_key: str, error: str) -> ErrorRecord:
        """Record a failure. Returns updated record with backoff_until set.

        If the work unit was previously cleared (success then failure again),
        the retry count resets to 1.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = self._records.get(work_unit_key)
            if existing is None:
                retry_count = 1
                first_seen = now
            else:
                retry_count = existing.retry_count + 1
                first_seen = existing.first_seen

            backoff = self._calculate_backoff(retry_count)
            record = ErrorRecord(
                first_seen=first_seen,
                last_seen=now,
                retry_count=retry_count,
                last_error=error,
                backoff_until=now + timedelta(seconds=backoff),
            )
            self._records[work_unit_key] = record
            return record

    def is_backed_off(self, work_unit_key: str) -> bool:
        """True if the work unit is in cooldown (should not be re-claimed yet)."""
        with self._lock:
            record = self._records.get(work_unit_key)
            if record is None:
                return False
            return datetime.now(timezone.utc) < record.backoff_until

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
        """True if error is exhausted AND backoff has expired.

        This means the work unit has used all retries and the last backoff
        period has elapsed, so it should be permanently marked as errored
        in the database.
        """
        with self._lock:
            record = self._records.get(work_unit_key)
            if record is None:
                return False
            return (
                record.retry_count >= self._max_retries
                and datetime.now(timezone.utc) >= record.backoff_until
            )

    def cleanup_expired(self) -> int:
        """Remove records older than TTL to prevent unbounded growth.

        Returns the number of records removed.
        """
        now = datetime.now(timezone.utc)
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