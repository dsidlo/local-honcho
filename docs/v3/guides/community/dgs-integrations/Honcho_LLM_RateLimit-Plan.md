# Honcho LLM Rate-Limit Strategy — Phased Plan (queue-deferral)

## Executive Summary

Honcho's LLM calls (`src/llm/api.py` → `honcho_llm_call`) currently retry **all**
failures identically with a blocking, in-process tenacity loop:
`stop_after_attempt(3)` + `wait_exponential(multiplier=1, min=4, max=10)`. A
provider **429** (rate limit — observed on `inceptionlabs/mercury-3`, message
`Rate limit reached: input token limit exceeded`) is treated like a timeout or a
5xx: three quick in-call retries, then the final attempt falls back to the backup
model (`qwen3.5:latest` on localhost). The deriver worker is **blocked** the entire
time.

This plan replaces that with a **queue-aware, non-blocking deferral strategy** that
matches how honcho's deriver already handles transient failures:

- On a 429, the deriver **signals "rate-limit in play"** and **records the start
  time** of the rate-limit condition.
- It does **not** mark the queue record as error — a throttle is not a failure.
- The worker **exits the record and moves to the next** in the queue, so other work
  keeps draining while the provider recovers.
- When the LLM responds again (cooldown elapsed), the affected records are
  re-attempted.

This reuses honcho's existing queue machinery (`queue_manager._handle_processing_error`
and the `error_tracker` backoff) rather than inventing a parallel path.

> **Design shift note:** The earlier draft targeted a blocking client-side retry
> (pi-style "wait ~60s × 15"). Review concluded that is *not* appropriate for
> honcho, because the deriver runs as a background queue worker — blocking one
> worker ~15 min on a single record stalls the whole pipeline. The queue-deferral
> approach is the correct fit, and this document supersedes the earlier draft.

---

## Behavior being implemented (the "what")

When the LLM call returns a 429:

1. **Signal** — the call path classifies the error as a rate limit and returns a
   well-defined signal (a typed exception or result flag) to the caller.
2. **Stamp start time** — the error tracker records `rate_limit_since = now` for
   the work unit (alongside ordinary backoff state).
3. **No error mark** — the queue item is **not** marked errored. It stays
   unprocessed, available to be re-claimed after the cooldown.
4. **Move on** — the worker releases the current record and pulls the **next**
   record from the queue. Other workspaces/records keep processing.
5. **Until LLM responds** — after the cooldown (fixed, configurable), the record is
   re-attempted; when the LLM answers, the rate-limit state is cleared and normal
   processing resumes.

---

## Background: current behavior (code-verified)

### In-process retry — `src/llm/api.py`

`honcho_llm_call` wraps the provider call with tenacity (two identical blocks, the
tool-less and the toolless path — lines ~297 and ~414):

```python
if enable_retry:
    decorated = retry(
        stop=stop_after_attempt(retry_attempts),        # 3 default
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_retry_callback,             # bumps current_attempt, logs
        reraise=True,
    )(decorated)
```

`before_retry_callback` increments `current_attempt` and logs
`Error on attempt N/3` then `Will retry with attempt N+1/3`.

### Provider/fallback selection — `src/llm/runtime.py`

`select_model_config_for_attempt` uses the **primary** on every attempt except the
last, which swaps to the resolved `fallback` when configured. So the final attempt
is the only one that runs on the backup model (`Final retry attempt 3/3: switching
from openai/mercury-3 to backup .../qwen3.5:latest`).

### Error classification — `src/llm/errors.py`

`as_upstream_error`/`raise_upstream_error` translate connection errors and `>=500`
into `UpstreamLLMError`. **4xx is deliberately left untranslated** — the comment
notes 429 "callers throttle on rather than retry blindly." Today nothing throttles
specially.

### The queue already has the right deferral hook — `src/deriver/queue_manager.py`

`_handle_processing_error` already implements the "leave unprocessed for retry"
pattern for transient errors:

```python
if is_retryable_error(error):
    attempts = await self._get_work_unit_retry_attempts(work_unit_key) + 1
    if attempts < MAX_RETRYABLE_ATTEMPTS:          # 3
        await self._set_work_unit_retry_attempts(work_unit_key, attempts)
        logger.warning("Transient error %s ... leaving items unprocessed for retry", ...)
        return True                                # release for later re-claim
# terminal path: mark_queue_item_as_errored(items[0], ...) and return False
```

`src/deriver/error_tracker.py` already carries `backoff_until`, `is_backed_off`,
and `record_error` per work unit. The 429 case can ride the *same* release-and-retry
path — we just need to add the rate-limit classification and the start-time stamp.

### Current deriver call site — `src/deriver/deriver.py` (~line 165)

```python
response = await honcho_llm_call(
    model_config=model_config, ..., enable_retry=True, retry_attempts=3, ...
)
```

---

## Goals / non-goals

### Goals
1. Treat provider **429s distinctly** from timeouts and 5xx.
2. **Never block a deriver worker** on a single record for minutes — defer instead.
3. **Never mark a rate-limited record as error** — it is a temporary throttle.
4. Track the **start time** of the rate-limit condition per work unit.
5. Keep the queue **draining** other records while the provider recovers.
6. Reuse `queue_manager`'s existing release-for-retry path and `error_tracker`
   backoff.
7. Everything configurable via env; full test coverage.

### Non-goals
- The deriver **queue-level escalation** bug fix (already covered by
  `Honcho-Retry-Escalation-Bug-Fix-Plan.md`).
- Changing which *models* are used or adding providers.
- Per-session/per-peer rate-limit state.
- Cross-process coordination (state is in-process per deriver; a restart clears it,
  by design).

---

## Design

### 1. Rate-limit classification

Add a public `is_rate_limit_error(exc)` in `src/llm/errors.py` (alongside
`as_upstream_error`). Reuse the existing `_status_of` approach (`status_code`/`code`
attribute probing) to detect HTTP 429 / `rate_limit_exceeded` / `RateLimitError`:
`anthropic`, `openai`, and `google-genai` all expose a numeric 429.

### 2. Client layer: minimal quick-retry (not the holding pattern)

Keep a **small** 429-aware retry *inside* `honcho_llm_call` to ride out sub-second
throttles — e.g. up to **3 attempts with a fixed 1–2s delay** — but **stop there**.
The call must never hold the worker for the ~15-min figure the old draft implied.
After the quick budget, the 429 propagates to the caller as the rate-limit signal.

### 3. Queue layer: deferral (the core change)

In `src/deriver/queue_manager.py`, branch `_handle_processing_error` on the new
classification, **before** the generic terminal path:

```python
if is_rate_limit_error(error):
    await self._mark_rate_limit(work_unit_key)     # records rate_limit_since
    await self._set_work_unit_retry_attempts(work_unit_key,
        await self._get_work_unit_retry_attempts(work_unit_key) + 1)
    logger.warning("Rate-limit in play for work unit %s (since %s); deferring ...", ...)
    return True                                    # release, do NOT mark errored
```

- **Returns True** → the existing caller releases the record and the worker moves to
  the next record. This is the "exit the record, keep the queue flowing" behavior.
- **No `mark_queue_item_as_errored`** call in this branch → the record stays
  unprocessed and un-errored.
- The retry-attempt counter is shared with the existing transient path, so a record
  that bounces between throttle and other transient errors keeps one budget.

### 4. Rate-limit start-time tracking

Extend `src/deriver/error_tracker.py`'s `ErrorRecord` with
`rate_limit_since: datetime | None` (default `None`). Add:

```python
def mark_rate_limit(self, work_unit_key: str) -> None:
    with self._lock:
        record = self._records.get(work_unit_key)
        now = datetime.now(UTC)
        if record is None:
            self._records[work_unit_key] = ErrorRecord(
                first_seen=now, last_seen=now,
                retry_count=0, last_error="rate_limit",
                backoff_until=now, rate_limit_since=now,
            )
        else:
            record.rate_limit_since = record.rate_limit_since or now
            record.last_seen = now
```

- First throttle sets `rate_limit_since`; later throttles preserve the original start
  time (so operators can see how long the condition has persisted).
- On success, `clear()` removes the record (and thus the stamp) — "until the LLM
  responds", the state is gone.
- Expose the stamp for metrics/logging (e.g. a Prometheus gauge
  `deriver_rate_limit_active_seconds`).

### 5. Re-attempt scheduling — gate the CLAIM path (critical)

Reuse the existing per-work-unit retry-attempt counter and `MAX_RETRYABLE_ATTEMPTS`
budget (currently 3). **But the deferral must be enforced in the claim/scheduling
query, not only in `_handle_processing_error`.** Because the worker's claim query
selects `processed=false` items, a record left merely "unprocessed" would be
re-claimed on the next poll → immediate 429 re-attempt → a busy-loop. Therefore:

- The **claim/scheduling query must exclude** work units whose `rate_limit_since`
  is still inside the cooldown window (i.e. `rate_limit_since IS NULL OR
  rate_limit_since < now - COOLDOWN`). Only when the window lapses should a
  deferred unit become eligible for re-claim again.
- This is the single point that makes deferral truly self-healing without operator
  intervention. Gate it here; `_handle_processing_error` records the stamp, the
  claim path reads it.
- Re-claim re-attempts the **same record** (do not spawn new rows — the current
  `is_retryable_error` path can spawn fresh rows; the deferral must not duplicate
  the work unit).

### 6. Config knobs (`src/config.py` → `LLMSettings` / `DeriverSettings`)

| Env var | Default | Meaning |
| --- | --- | --- |
| `LLM_RETRY_RATELIMIT_ENABLED` | `true` | Master switch for the 429 strategy |
| `LLM_RETRY_RATELIMIT_QUICK_RETRIES` | `3` | In-call quick retries before signaling (client layer) |
| `LLM_RETRY_RATELIMIT_QUICK_DELAY_MS` | `1500` | Delay between quick retries |
| `LLM_RETRY_RATELIMIT_COOLDOWN_SEC` | `60` | Queue-level cooldown before re-claiming a deferred record |
| `LLM_RETRY_RATELIMIT_MAX_DEFERRALS` | `5` | Cap on consecutive deferrals before the record is surfaced as stuck |
| `LLM_RETRY_RATELIMIT_ACTIVE_SINCE_METRIC` | `true` | Emit the active-since metric |

### 7. Termination/backstop

A record that stays rate-limited beyond `MAX_DEFERRALS` (or the shared retry
budget) is **not** marked errored; instead it is logged and left queued (we do not
want a throttle to destroy data). An operator-facing log line summarizes
`rate_limit_since` and how long the condition has persisted.

---

## Phases

### Phase 0 — Spike & invariants (design validation)

**Tasks**
- Confirm the 429 exception shapes for anthropic/openai/google-genai in
  `src/llm/errors.py`; write `is_rate_limit_error` + unit tests.
- Verify `_handle_processing_error`'s `return True` path actually releases the
  worker to pull the next record (read `process_work_unit` loop).
- Confirm `error_tracker` is per-`QueueManager` and in-memory (restart clears state
  — acceptable, document it).

**Exit criteria**: `is_rate_limit_error` passes for all three SDK shapes; the
release-and-move-on path is confirmed; no hidden state expected across restarts.

### Phase 1 — Config plumbing (`src/config.py`)

**Tasks**
- Add the `LLMSettings`/`DeriverSettings` fields from the table.
- Unit tests for defaults + env overrides.

**Exit criteria**: settings load from env; defaults match the table.

### Phase 2 — Classification helper (`src/llm/errors.py`)

**Tasks**
- Add `is_rate_limit_error(exc)` reusing `_status_of`; keep 4xx untranslated.
- Tests in `tests/llm/test_upstream_errors.py`.

**Exit criteria**: 429 detected across backends; non-429 untouched.

### Phase 3 — Client layer quick-retry (`src/llm/api.py`)

**Tasks**
- Replace the blanket `wait_exponential(4…10)` with a per-failure-class wait: 429 →
  `wait_fixed(QUICK_DELAY_MS)` × `QUICK_RETRIES`; else the current exponential.
- Keep `before_retry_callback` logging distinct: `Rate-limit ... quick-retrying
  N/3` vs `Error on attempt N/3 ... Will retry`.
- After the quick budget, let the 429 propagate (do not fall back on it).

**Exit criteria**: a mocked 429 quick-retries then surfaces as a rate-limit signal;
timeout/5xx keep current exponential + fallback.

### Phase 4 — Error-tracker rate-limit stamp (`src/deriver/error_tracker.py`)

**Tasks**
- Add `rate_limit_since` to `ErrorRecord`; add `mark_rate_limit()`; clear on success.
- Expose `rate_limit_since` for logging/metadata.
- Unit tests: first throttle stamps; later throttles preserve; `clear()` removes.

**Exit criteria**: `rate_limit_since` tracked per work unit and cleared on success.

### Phase 5 — Queue-level deferral (`src/deriver/queue_manager.py`)

**Tasks**
- In `_handle_processing_error`, branch on `is_rate_limit_error(error)` before the
  terminal path: call `mark_rate_limit`, increment the work-unit retry counter,
  log, and `return True` (release, do **not** mark errored).
- Respect `LLM_RETRY_RATELIMIT_MAX_DEFERRALS`; emit the backstop log.
- Add a Prometheus metric for `deriver_rate_limit_active_seconds`.
- **Claim-path cooldown gate (critical, required for self-healing):** extend
  `WorkUnitErrorTracker` with an accessor that reports whether a work unit's
  `rate_limit_since` is still inside the cooldown window
  (`LLM_RETRY_RATELIMIT_COOLDOWN_SEC`), and filter those units out of **both**
  `_get_and_claim_work_units_fifo` and `_get_and_claim_work_units_weighted`
  before claiming (logging when a unit is skipped). Without this gate, a released
  record is immediately re-claimed on the next poll and the worker busy-loops
  re-attempting the throttled provider — the exact failure this strategy exists
  to prevent. The DB-level `unclaimed_work_unit_clause` cannot express this
  (rate-limit state is in-memory per `QueueManager`), so the gate belongs in the
  claim path's Python layer, after rows are selected and before they are claimed.

**Exit criteria**: a 429 leaves the record unprocessed and un-errored, releases the
worker, and the queue continues with other records; the terminal path is untouched
for non-429; both claim paths exclude rate-limited units inside the cooldown
window, so a deferred record is not re-claimed until the cooldown lapses.

### Phase 6 — Deriver call-site wiring (`src/deriver/deriver.py`)

**Tasks**
- Keep `honcho_llm_call(..., enable_retry=True)`; let `retry_attempts` reflect the
  new quick-retry knob so the client layer doesn't hold the worker.
- Ensure the propagated 429 reaches `queue_manager._handle_processing_error` (i.e.
  it is not swallowed/translated before that point).
- Confirm queue-level escalation (`error_tracker`/`queue_manager`) is unaffected.

**Exit criteria**: a deriver 429 defers the record (no qwen downgrade, no error
mark) and other work continues.

### Phase 7 — Tests & validation

**Tasks**
- Unit tests:
  - `is_rate_limit_error` for 3 backends.
  - Client quick-retry (429 → quick fixed wait → signal; timeout/5xx → exponential).
  - `mark_rate_limit` / `rate_limit_since` lifecycle.
  - `_handle_processing_error` 429 branch: returns True, no errored mark, counter
    incremented, cooldown/max-deferral backstop.
  - Env knob overrides (disabled strategy, cooldown, max deferrals).
  - Claim-gate: a rate-limited work unit inside the cooldown is NOT claimed by
    fifo or weighted scheduling; once the cooldown lapses it becomes eligible again.
- Integration: run the full deriver suite with a mocked throttling provider and
  assert: record left unprocessed, worker pulls a different record, no errored
  mark, `rate_limit_since` set.
- Run `uv run pytest tests/llm/ tests/deriver/`.

**Exit criteria**: full green; coverage maintained; the integration test shows the
deferral behavior.

### Phase 8 — Documentation & rollback

**Tasks**
- Document new env vars; note the operator-facing log and metric.
- Rollback path: set `LLM_RETRY_RATELIMIT_ENABLED=false` → behavior returns to the
  current 3-attempt exponential + fallback, no code revert needed.

**Exit criteria**: docs updated; rollback verified via env flip.

---

## Reference: phase/exit summary

| Phase | Output | Exit check |
| --- | --- | --- |
| 0 | Classification helper + design note | 429 classified across 3 SDKs; release path confirmed |
| 1 | Config knobs | env overrides + defaults tested |
| 2 | `is_rate_limit_error` | 429 detected; non-429 untouched |
| 3 | Client quick-retry | 429 signals fast; timeout/5xx unchanged |
| 4 | `rate_limit_since` stamp | tracked + cleared on success |
| 5 | Queue-level deferral | release, no error mark, queue keeps draining; claim paths gate on cooldown |
| 6 | Deriver wiring | 429 defers; qwen not used; other work continues |
| 7 | Tests + integration | `pytest tests/llm tests/deriver` green |
| 8 | Docs + rollback | env flip reverts behavior |

---

## Acceptance criteria

1. A provider 429 causes the record to be **left unprocessed and un-errored**, with
   `rate_limit_since` recorded.
2. The worker **moves on to the next record** and does not block on the throttled
   one.
3. No fallback model downgrade occurs on a 429.
4. After the cooldown, the record is re-attempted and processed once the LLM
   responds; `rate_limit_since` is cleared on success.
5. `LLM_RETRY_RATELIMIT_ENABLED=false` reproduces today's behavior exactly.
6. All `tests/llm/` and `tests/deriver/` tests pass; coverage maintained.
7. The journal shows a legible `Rate-limit in play ... deferring` line and no
   `switching ... to backup` on 429.

## Out of scope (explicit)

- Deriver queue-level retry/escalation bug fix (`error_tracker.py`,
  `queue_manager.py`) — see `Honcho-Retry-Escalation-Bug-Fix-Plan.md`.
- Per-peer/per-session rate-limit state or cross-process coordination.
- Adding providers or changing the model/fallback topology.
