# Honcho Retry Escalation Bug: Fix Plan

## Executive Summary

`WorkUnitErrorTracker.should_escalate()` can never return `True` on the production
call path. As a result, the "permanently mark the queue item as errored" branch in
`QueueManager._handle_processing_error()` is dead code. Every failing work unit
retries forever, its retry counter grows unbounded, and the DB never records the
failure.

This is a latent defect, not currently a data-loss defect: nothing is lost, but the
queue cannot converge and failures are invisible to the DB.

**Fix**: make the escalation decision atomic with the error record, and stop setting
a future cooldown when retries are already exhausted.

---

## The Defect

### Code under test

`src/deriver/error_tracker.py` (144 lines), `src/deriver/queue_manager.py` (1337 lines).
Repo and installed copies (`~/.local/lib/honcho/src/`) are byte-identical.

`record_error()` (`error_tracker.py:54`) writes the cooldown at line 76:

```python
backoff = self._calculate_backoff(retry_count)
record = ErrorRecord(
    ...
    backoff_until=now + timedelta(seconds=backoff),
)
```

`_calculate_backoff()` (`error_tracker.py:142`):

```python
backoff = self._base_backoff * (2 ** (retry_count - 1))
return min(backoff, self._max_backoff)
```

`should_escalate()` (`error_tracker.py:102`):

```python
return (
    record.retry_count >= self._max_retries
    and datetime.now(timezone.utc) >= record.backoff_until
)
```

### Why it cannot fire

`config.py` declares `BASE_BACKOFF_SEC: float = Field(default=5.0, gt=0.0, ...)`.
The `gt=0.0` constraint makes `backoff` strictly positive for every retry count, so
`backoff_until` is strictly in the future at the moment the record is written.

The single call site (`should_escalate` appears exactly twice in the tree: its
definition and `queue_manager.py:782`) reads the record immediately after writing it:

```python
769:  record = self.error_tracker.record_error(work_unit_key, error_msg)
...     # logging only, no sleep
782:  if self.error_tracker.should_escalate(work_unit_key):
```

`now < backoff_until` holds unconditionally. The `and` short-circuit is never reached
in a state where the first operand is `True` and the second is also `True`.

### Corroboration: `is_exhausted()` is dead

`is_exhausted()` (`error_tracker.py:89`) is the predicate that actually encodes
"retries are used up". It is defined but never called: the only tracker methods
`queue_manager.py` invokes are `clear` (892, 924), `record_error` (769),
`is_backed_off` (299, 335, 843), `should_escalate` (782), `cleanup_expired` (707).
The intended exhaustion check was never wired up.

---

## Evidence from the field

Observed in `journalctl --user-unit=honcho-deriver --since 2026-09-30`:

| Signal | Count |
| --- | --- |
| `will be retried` (retry branch taken) | 84 |
| `WRR Filtered ... backed-off` | 2005 |
| `marking as permanently errored` (escalation branch) | **0** |
| Distinct work units stuck | 52 |

Retry counters are monotonic and unbounded, which is only possible if escalation
never fires:

```
count  retry_count
    11  1     9  2    8  3    8  4    7  5    5  6    5  7    5  8    5  9
     5 10     5 11    3 12    2 13    2 14    2 15    1 16    1 17
```

The `retry N/3` rendering (`queue_manager.py:774`) is itself misleading: the
denominator is `_max_retries`, so `retry 17/3` reads as "17 of 3". The counter is
climbing past its own cap.

The 17 ceiling is not a retry cap. It is `ERROR_TRACKER_TTL_SEC = 3600` doing the
bounding: with `base=5.0` doubling and a 300s cap, the cooldowns sum to ~415s over
7 retries, then 300s each, so ~17 retries fit inside one TTL window before
`cleanup_expired` (`queue_manager.py:707`) drops the record and the next error
resets `retry_count` to 1. The loop is bounded by TTL, not by `MAX_RETRIES`.

### Why the existing test passes

`tests/deriver/test_error_tracker.py:95` `test_should_escalate_when_exhausted_and_backoff_expired`
constructs `base_backoff=0.01` and calls `time.sleep(0.05)` between `record_error`
and `should_escalate`. The test sleeps; production does not. The test hand-crafts
exactly the state the production path never reaches, so it is green over a broken
path. This is the reason the bug shipped.

---

## Fix Plan

### Step 1: make the decision atomic and reachable

Replace the two-step `record_error` + `should_escalate` handshake with one
lock-protected decision. Two reasons this is required, not just convenient:

1. Reachability: when `retry_count >= max_retries` there is no cooldown left to wait
   for, so `backoff_until` should be `now`, not `now + backoff`.
2. Race: `record_error` and `should_escalate` take the lock separately. With
   `DERIVER__WORKERS=8` in the deriver override, another worker's `clear()`
   (`queue_manager.py:892`/`924`) can land between them and flip the decision.

Add an `escalate` field to `ErrorRecord` and compute it inside `record_error`:

```python
escalate = retry_count >= self._max_retries
backoff = 0.0 if escalate else self._calculate_backoff(retry_count)
```

Keep `should_escalate()` as a thin accessor over `record.escalate` so the existing
call site and tests keep compiling, and keep `is_exhausted()` as the pure predicate
so the two are no longer redundant.

### Step 2: fix the misleading log

`queue_manager.py:774` and `:803` render `retry_count` against `_max_retries`
without exposing that the counter can exceed it. Render
`min(retry_count, max_retries)` alongside the raw count, or log
`retry {retry_count} (cap {max_retries})`.

### Step 3: test the production path, not a hand-crafted one

Add to `tests/deriver/test_error_tracker.py` a test that asserts the invariant the
production path depends on, with default config and no sleep:

- after exactly `max_retries` calls to `record_error`, `should_escalate()` is `True`
  immediately, with no intervening `sleep`
- `backoff_until == last_seen` when exhausted
- retry counter does not exceed `max_retries` while a record is live under TTL

Keep the existing `test_should_escalate_when_exhausted_and_backoff_expired`; it still
covers the cooldown semantics for the non-exhausted case.

### Step 4: verify the escalation branch actually runs

`mark_queue_item_as_errored` (`queue_manager.py:1278`) has never executed. Exercise it
end-to-end before shipping: force an embedding failure (the current GPU-contention
condition reproduces it on demand) and confirm a queue item transitions to
`processed=True, error=...`.

---

## Out of scope, deliberately

The GPU contention that surfaced this bug is a separate problem and should not be
fixed by this change:

- `Strata --serve` holds 14636 MiB of the 16384 MiB RTX A5000; `qwen3-embedding:8b`
  needs a 1860430720-byte (1774 MiB) CUDA0 compute buffer and gets 1107 MiB.
- `llama-server` dies on init, ollama returns HTTP 500, honcho exhausts its 3
  internal attempts (`[EMBED-FINAL-FAILURE]`), and the queue-level retry loop above
  is what then spins.
- CPU is not implicated; this is purely a VRAM reservation collision.

Fixing escalation makes the queue stop and record the failure instead of spinning
forever. It does not make embeddings succeed. The separate pause-Strata-then-backfill
question (the tailer already keeps byte-offset state in
`~/.cache/jcode-honcho/state.json` and spools to `~/.cache/jcode-honcho-spool`,
7 files / 68 KB pending) is a different plan.

## Acceptance criteria

1. `should_escalate` returns `True` on the production path after `max_retries` errors,
   with no sleep.
2. `marking as permanently errored` appears in the journal under a forced failure.
3. Retry counters are bounded by `MAX_RETRIES`, not by `ERROR_TRACKER_TTL_SEC`.
4. No regression in the 20 existing `test_error_tracker.py` tests.
