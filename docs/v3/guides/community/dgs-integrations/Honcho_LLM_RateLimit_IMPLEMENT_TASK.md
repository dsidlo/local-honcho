# Implementation Task — Honcho LLM Rate-Limit Strategy

Implement the phased plan in `docs/v3/guides/community/dgs-integrations/Honcho_LLM_RateLimit-Plan.md` in this repo (/home/dsidlo/workspace/honcho, branch pi-dev).

Read the plan fully first. Then implement its phases in order, grounded in these exact code locations:

- `src/llm/errors.py` — add a public `is_rate_limit_error(exc)` classifier (reuse the `_status_of` attribute-probing approach; detect 429 / `rate_limit_exceeded` / `RateLimitError` for anthropic/openai/google-genai). Keep 4xx untranslated.
- `src/llm/api.py` — in `honcho_llm_call`, replace the blanket tenacity wait with a per-failure-class wait: 429 gets a bounded quick-retry (default ~3 tries, ~1.5s fixed) then propagates as the rate-limit signal; timeout/5xx keep the existing exponential 4–10s path and fallback behavior. Do NOT hold the worker for minutes.
- `src/deriver/error_tracker.py` — add `rate_limit_since: datetime | None` to `ErrorRecord`; add `mark_rate_limit(work_unit_key)` that stamps on first throttle and preserves the original stamp on later throttles; `clear()` removes it on success.
- `src/deriver/queue_manager.py` — in `_handle_processing_error`, branch on `is_rate_limit_error(error)` BEFORE the terminal path: call `mark_rate_limit`, increment the work-unit retry counter, log a "Rate-limit in play … deferring" line, and return True (release the record, do NOT call `mark_queue_item_as_errored`). Respect an `LLM_RETRY_RATELIMIT_MAX_DEFERRALS` backstop.
- `src/deriver/deriver.py` — ensure the propagated 429 reaches `_handle_processing_error` un-swallowed and `retry_attempts` reflects the quick-retry knob.
- `src/config.py` — add the `LLM_RETRY_RATELIMIT_*` knobs exactly as the plan's table specifies (`ENABLED`, `QUICK_RETRIES`, `QUICK_DELAY_MS`, `COOLDOWN_SEC`, `MAX_DEFERRALS`, `ACTIVE_SINCE_METRIC`) with the plan's defaults.

Follow the repo's conventions (AGENTS.md): use `uv run` for pytest/ruff/basedpyright; line length 88; Google-style docstrings; never hold a DB session during external calls; snake_case/PascalCase; use `tracked_db` for short-lived DB-only ops.

Write tests: unit tests for the classifier (3 backend shapes), client quick-retry (429 signals fast; timeout/5xx unchanged), `mark_rate_limit`/`rate_limit_since` lifecycle (stamp, preserve, clear), and the `_handle_processing_error` 429 branch (returns True, no errored mark, counter incremented, cooldown/max-deferral backstop). Run gates: `uv run pytest tests/llm/ tests/deriver/`, `uv run ruff check src/`, `uv run basedpyright`, `uv run ruff format --check src/`.

Do NOT run the full suite (`tests/`) alone if it pulls unrelated flaky integrations; focus on `tests/llm/` and `tests/deriver/`. Do NOT touch unrelated working-tree files (jcode/README.md, jcode/tests/test_turn_jsay.py, .idea.bk/, .jcode_scratch/, .junie/, eng.traineddata, staging_today/, test_results.txt, or this task file). Only modify the files listed above plus new test files. Do not commit.

When done, report per phase: file changed + summary, test/ruff/right/format results, and deviations from the plan.
