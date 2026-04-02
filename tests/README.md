# Honcho Testing Guide

This document provides guidance for running and debugging tests in the Honcho codebase based on real-world experience.

## Quick Start

```bash
# Run all tests (parallel + sequential with fork)
./scripts/run-tests.sh

# Run specific test file
./scripts/run-tests.sh tests/routes/test_messages.py

# Run only sequential tests (with fork isolation)
./scripts/run-tests.sh --sequential-only

# Run without parallelization (slower, but useful for debugging)
./scripts/run-tests.sh --no-parallel

# Skip tests requiring embeddings (faster)
./scripts/run-tests.sh --no-embedding
```

## Test Structure

### Test Organization

| Directory | Purpose |
|-----------|---------|
| `tests/routes/` | API route endpoint tests |
| `tests/sdk/` | Python SDK integration tests |
| `tests/sdk_typescript/` | TypeScript SDK tests |
| `tests/deriver/` | Background processing (queue, deriver) tests |
| `tests/dialectic/` | Dialectic/chat API tests |
| `tests/utils/` | Utility function tests |

### Test Markers

Tests are categorized using pytest markers:

- `@pytest.mark.sequential` - Tests that must run sequentially (not in parallel), typically due to:
  - Shared state (databases, caches)
  - AsyncIO event loop sensitivity
  - External service interactions

- `@pytest.mark.integration` - Tests requiring external services (Redis, PostgreSQL)

- `@pytest.mark.embedding` - Tests requiring embedding service (Ollama or mocked)

## Critical Testing Learnings

### 1. AsyncIO Event Loop Isolation

**Problem:** When running asyncio tests sequentially (`-n 0`), tests can fail with "Event loop is closed" errors.

**Root Cause:** 
- `asyncio_default_fixture_loop_scope = "session"` creates one event loop per pytest session
- SDK tests use `httpx.AsyncClient(transport=ASGITransport(app=...))` which holds references to the ASGI app
- Session-scoped fixtures + sequential execution can pollute event loop state

**Solution:** Use `--forked` for sequential tests to run each test in a separate process:

```bash
# In scripts/run-tests.sh - sequential tests run with --forked
uv run pytest tests/ -m sequential --forked
```

This ensures complete event loop isolation without changing fixture scopes.

### 2. FLUSH_ENABLED and Token Batching Tests

**Problem:** Tests like `test_forced_batching_waits_for_threshold` fail when `FLUSH_ENABLED=True`.

**Root Cause:** In `src/deriver/queue_manager.py`:
```python
if not settings.DERIVER.FLUSH_ENABLED and batch_max_tokens > 0:
    query = query.where(or_(
        ~work_units_subq.c.work_unit_key.startswith(representation_prefix),
        func.coalesce(token_stats_subq.c.total_tokens, 0) >= batch_max_tokens,
    ))
```

When `FLUSH_ENABLED=True`, the token threshold filter is **bypassed entirely**, allowing all work units to be claimed regardless of token count.

**Solution:** Tests verifying batching behavior must patch `FLUSH_ENABLED`:

```python
@pytest.mark.asyncio
async def test_forced_batching_waits_for_threshold(...):
    # ... setup code ...
    
    with patch.object(settings.DERIVER, "FLUSH_ENABLED", False):
        claimed = await qm.get_and_claim_work_units()
    
    assert rep_work_unit_key not in claimed  # Now properly filtered
```

### 3. Test Configuration Sources

**Priority order for test settings:**
1. `pytest.patch.object()` (highest priority)
2. `.env.test.local` (if exists)
3. `.env.test`
4. Default config values (`src/config.py`)
5. `config.toml` (lowest priority)

**Important:** The test script loads `.env.test` at startup, so runtime patches take precedence for test-specific behavior.

### 4. Parallel vs Sequential Strategy

**Best Practice:** Run tests in two phases:

1. **Parallel phase** (`-n auto`): All non-sequential tests run concurrently using pytest-xdist
2. **Sequential phase** (`--forked`): Sequential tests run one-per-process for isolation

```bash
# Parallel tests (fast, concurrent)
uv run pytest tests/ -n auto -m "not sequential"

# Sequential tests (isolated, one per process)
uv run pytest tests/ -m sequential --forked
```

**Why not just `-n auto` for everything?**
- Sequential tests grouped by `--dist=loadgroup` still share a worker's event loop
- `--forked` provides stronger isolation than loadgroup distribution

### 5. SDK Test Patterns

**Sync vs Async Parameterization:**

SDK tests often parametrize to test both sync and async interfaces:

```python
@pytest.fixture(params=["sync", "async"])
def client_fixture(request, ...):
    if request.param == "sync":
        return honcho_sync_test_client, "sync"
    return honcho_async_test_client, "async"
```

**Note:** When testing with `-n 0` (no parallelization), the sync test runs first and can interfere with the async test's event loop. This is why `--forked` is essential for sequential test runs.

### 6. Common Failure Patterns

| Error | Likely Cause | Fix |
|-------|--------------|-----|
| "Event loop is closed" | Event loop pollution between tests | Use `--forked` for sequential tests |
| "Connection refused: localhost:11434" | Ollama not running or embedding not mocked | Set `HONCHO_TEST_USE_OLLAMA=0` or use `--no-embedding` |
| Assertion errors in batching tests | `FLUSH_ENABLED=True` bypasses filters | Patch to `FLUSH_ENABLED=False` in test |
| "RuntimeError: cannot schedule new futures after shutdown" | Async client not properly closed | Check `finally: await client.aclose()` in fixtures |

## Debugging Tips

### Enable Verbose Output

```bash
# Run single test with full traceback
uv run pytest tests/path/to/test_file.py::test_function -vvv --tb=long

# Show test durations (find slow tests)
uv run pytest tests/ --durations=10
```

### Check Test Configuration

```python
# Add to test for debugging
from src.config import settings
print(f"FLUSH_ENABLED: {settings.DERIVER.FLUSH_ENABLED}")
print(f"BATCH_MAX_TOKENS: {settings.DERIVER.REPRESENTATION_BATCH_MAX_TOKENS}")
```

### Test Against Specific Python Version

```bash
# Check current Python version
uv run python --version

# Pin Python version in pyproject.toml if needed
requires-python = ">=3.10,<3.12"
```

## CI/CD Considerations

When running tests in CI:

1. **Always use the runner script:** `./scripts/run-tests.sh` handles the parallel/sequential split
2. **Set environment variables:** Source `.env.test` before running tests
3. **Install dependencies:** Run `uv sync` before testing
4. **Database setup:** Ensure PostgreSQL and Redis are available and migrations are run

Example CI workflow:

```yaml
- name: Run tests
  run: |
    export $(grep -v '^#' .env.test | xargs)
    uv sync
    ./scripts/run-tests.sh
```

## Related Documentation

- `CLAUDE.md` - Project overview and architecture
- `tests/sdk_typescript/README.md` - TypeScript SDK testing specifics
- `tests/deriver/README.md` - Background processing test patterns
- `tests/bench/README.md` - Benchmark testing guide
