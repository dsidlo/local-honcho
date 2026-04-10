# Proposal: Queue Item Retry with In-Memory Error Queue

## Problem

### Current Flow (Broken)

```
process_work_unit()
  └─ process_representation_batch()
       └─ for observer in observers:
            └─ save_representation()  ← embed call inside
                 └─ [FAILS: Ollama 500]
                 └─ caught, logged, swallowed
       └─ mark_queue_items_as_processed()  ← ALWAYS runs
```

**Result**: Queue items are marked `processed=true` regardless of whether the
representation was actually saved. Failed observations are silently lost.
With multiple observers, a failure for **one** observer still marks the
items as processed for **all** observers.

### Observed Impact

- 18 Ollama embed 500s in 6 hours → 18 lost representation saves for `agent-pi-mono`
- 91 total `error` rows in queue table → all marked `processed=true`, never retried
- No mechanism to re-attempt failed work

---

## Proposed Design

### Core Principle

**On error, reset the work unit to unprocessed and back off.** An in-memory
error tracker prevents infinite retry loops while allowing transient failures
to self-heal.

### Component 1: `WorkUnitErrorTracker` (In-Memory)

Per-process, thread-safe error tracker keyed by `work_unit_key`.

```python
@dataclass
class ErrorRecord:
    first_seen: datetime
    last_seen: datetime
    retry_count: int
    last_error: str
    backoff_until: datetime  # cooldown period

class WorkUnitErrorTracker:
    """In-memory tracker for failed work units with exponential backoff."""

    def __init__(
        self,
        max_retries: int = 3,
        base_backoff: float = 5.0,      # seconds
        max_backoff: float = 300.0,      # 5 min cap
        ttl: float = 3600.0,             # forget after 1 hour
    ): ...

    def record_error(self, work_unit_key: str, error: str) -> ErrorRecord:
        """Record a failure. Returns updated record with backoff_until."""

    def is_backed_off(self, work_unit_key: str) -> bool:
        """True if work unit is in cooldown (should not be re-claimed yet)."""

    def is_exhausted(self, work_unit_key: str) -> bool:
        """True if retry_count >= max_retries (permanent failure)."""

    def clear(self, work_unit_key: str) -> None:
        """Remove record on successful processing."""

    def should_escalate(self, work_unit_key: str) -> bool:
        """True if error is exhausted AND backoff has expired → mark errored in DB."""

    def cleanup_expired(self) -> None:
        """Remove records older than TTL (prevents unbounded growth)."""
```

**Backoff schedule**: `base_backoff * 2^(retry_count - 1)`
- Retry 1: 5s
- Retry 2: 10s
- Retry 3: 20s → exhausted

### Component 2: Queue Item State Change — `processed=false` on Error

**New method** in `QueueManager`:

```python
async def reset_queue_items_for_retry(
    self,
    items: list[QueueItem],
    work_unit_key: str,
    error: str,
) -> None:
    """Reset queue items to unprocessed so they can be re-claimed.
    
    Only called when the error tracker says retries remain.
    The queue item's `error` field is updated with the latest error
    for observability, but `processed` stays False.
    """
    async with tracked_db("reset_queue_items_for_retry") as db:
        item_ids = [item.id for item in items]
        await db.execute(
            update(models.QueueItem)
            .where(models.QueueItem.id.in_(item_ids))
            .where(models.QueueItem.work_unit_key == work_unit_key)
            .values(processed=False, error=error[:65535])
        )
        # Keep ActiveQueueSession alive so the work unit stays "in progress"
        await db.execute(
            update(models.ActiveQueueSession)
            .where(models.ActiveQueueSession.work_unit_key == work_unit_key)
            .values(last_updated=func.now())
        )
        await db.commit()
```

### Component 3: Revised Processing Flow

```
process_work_unit()
  └─ try:
       └─ process_representation_batch()  ← may fail partially
       └─ error_tracker.clear(work_unit_key)
       └─ mark_queue_items_as_processed()
     except Exception:
       └─ record = error_tracker.record_error(work_unit_key, error_msg)
       └─ if record.retry_count <= max_retries:
            └─ reset_queue_items_for_retry()  ← processed=False
            └─ release work unit ownership (let another worker pick it up later)
            └─ break (stop processing this work unit for now)
       └─ else:
            └─ mark_queue_item_as_errored()  ← processed=True, error=msg
            └─ break
```

### Component 4: WRR Integration — Skip Backed-Off Work Units

In `_get_and_claim_work_units_weighted()` (and FIFO equivalent), after
querying pending work units, filter out any that the error tracker says
are in backoff:

```python
# In _query_task_type or after getting work_units list:
work_unit_keys = [parse_work_unit_key(wu).work_unit_key for wu in work_units]
available = [
    wu for wu, key in zip(work_units, work_unit_keys)
    if not self.error_tracker.is_backed_off(key)
]
```

This prevents workers from re-claiming a work unit that just failed and is
still in cooldown.

### Component 5: Representations — Re-raise on Observer Failure

The current deriver loops over observers and swallows per-observer failures:

```python
# CURRENT: error is caught and lost
for observer in observers:
    try:
        await representation_manager.save_representation(...)
    except Exception as e:
        logger.error("Failed to save representation for observer %s: %s", observer, e)
```

**Proposed**: Re-raise on failure so the queue manager's error handling kicks in:

```python
# PROPOSED: re-raise on failure
for observer in observers:
    try:
        await representation_manager.save_representation(...)
    except Exception as e:
        logger.error("Failed to save representation for observer %s: %s", observer, e)
        raise  # let queue manager handle it
```

This is safe because:
- **99.8% of work units have a single observer** — no partial success possible
- The rare multi-observer case (0.2%) uses the same Ollama endpoint for all observers;
  an infrastructure failure affects all equally, so there's no partial failure scenario
- Already-succeeded observers are protected by the existing `[DUPLICATE DETECTION]` logic
  on retry, so there's no data duplication risk

---

## Configuration

Add to `DeriverSettings` in `config.py`:

```python
class DeriverRetrySettings(BaseModel):
    """Retry and error handling for failed work units."""
    MAX_RETRIES: int = Field(default=3, description="Max retry attempts per work unit")
    BASE_BACKOFF_SEC: float = Field(default=5.0, description="Initial backoff in seconds")
    MAX_BACKOFF_SEC: float = Field(default=300.0, description="Maximum backoff cap")
    ERROR_TRACKER_TTL_SEC: float = Field(default=3600.0, description="Forget errors after this long")
```

Env overrides:
```bash
DERIVER__RETRY__MAX_RETRIES=3
DERIVER__RETRY__BASE_BACKOFF_SEC=5.0
DERIVER__RETRY__MAX_BACKOFF_SEC=300.0
DERIVER__RETRY__ERROR_TRACKER_TTL_SEC=3600.0
```

---

## Migration: No Schema Changes Required

The existing `error` column on `queue` already stores error text. The only
behavioral change is:

| Before | After |
|--------|-------|
| `processed=True, error="..."` | First N retries: `processed=False, error="..."` |
| | After exhaustion: `processed=True, error="..."` (same as before) |

No Alembic migration needed. The `processed=False + error IS NOT NULL` state
is the new intermediate that doesn't break any existing queries (all queries
filter on `processed=False` without checking `error`).

---

## Error Classification (Future Improvement)

Not all errors should be retried. A simple classification:

| Error Type | Retry? | Example |
|-----------|--------|---------|
| **Transient** | ✅ Yes | Ollama 500, network timeout, connection refused |
| **Data** | ❌ No | Orphaned work unit, message deleted, schema mismatch |
| **Logic** | ❌ No | Token limit exceeded, invalid payload format |

For now, we retry all errors with the backoff mechanism. A future
`ErrorClassifier` can mark certain exception types as non-retryable,
bypassing the error tracker and going straight to `processed=True, error=...`.

---

## Summary of Changes

1. **`WorkUnitErrorTracker`** — new class in `src/deriver/error_tracker.py`
2. **`reset_queue_items_for_retry()`** — new method on `QueueManager`
3. **Revised `process_work_unit()`** — uses error tracker + reset-on-error
4. **WRR/FIFO claim filtering** — skip backed-off work units
5. **`DeriverRetrySettings`** — new config section in `config.py`
6. **`process_representation_batch()`** — re-raise on observer failure

No DB migration. No API changes. Fully backward-compatible.