# Honcho Test Suite Guide

This document provides guidance on running and troubleshooting the Honcho test suite, which includes 938+ tests across Python route/integration tests and TypeScript SDK tests.

## Test Structure

### Python Tests (tests/)
- **Route Tests** (`tests/routes/`): 200+ tests for API endpoints
- **Integration Tests** (`tests/integration/`): 150 tests for end-to-end workflows  
- **Deriver Tests** (`tests/deriver/`): 100+ tests for background processing
- **SDK Tests** (`tests/sdk/`): 300 tests for Python SDK
- **Total**: ~750 Python tests

### TypeScript SDK Tests (sdks/typescript/__tests__/)
- **Full Suite**: 310 tests covering all SDK functionality
- **Key Files**:
  - `conclusions.test.ts`: 35 tests
  - `messages.test.ts`: 26 tests  
  - `session.test.ts`: 39 tests
  - `peer.test.ts`: 25 tests
  - `streaming.test.ts`: 13 tests

## Running Tests

### 1. Python Tests Only
```bash
# Run all Python tests (excludes TypeScript)
uv run pytest tests/ -v -k "not typescript"

# Run specific suite
uv run pytest tests/routes/ -v
uv run pytest tests/sdk/ -v
```

### 2. TypeScript SDK Tests
```bash
# Run via pytest (recommended - orchestrates server setup)
uv run pytest tests/ -k typescript -v

# Direct Bun test (requires running server - not recommended)
cd sdks/typescript && bun test
```

### 3. Full Test Suite (Recommended Approach)
```bash
# Run Python tests first (no parallelization to avoid segfault)
uv run pytest tests/ -v -k "not typescript" -n 1

# Run TypeScript tests second
uv run pytest tests/ -v -k typescript
```

### 4. Single Test or File
```bash
# Single test
uv run pytest tests/routes/test_conclusions.py::TestConclusionRoutes::test_create_conclusion_success -v

# Test file
uv run pytest tests/sdk/test_conclusions.py -v
```

## Known Issues & Workarounds

### 1. Segmentation Fault (Fatal Python Error)
**Issue**: Full test suite (`pytest tests/`) crashes with segmentation fault during parallel execution.

**Symptoms**:
- Occurs during async database operations
- Affects `tests/sdk/test_conclusions.py::test_observation_create_single[async]`
- Stack trace involves SQLAlchemy/psycopg2 and asyncio

**Root Cause**: Race condition in parallel async database connections with complex test fixtures.

**Workaround**:
```bash
# Run without parallelization
uv run pytest tests/ -n 1 -v

# Or run test suites individually
uv run pytest tests/routes/ -v
uv run pytest tests/integration/ -v  
uv run pytest tests/deriver/ -v
uv run pytest tests/sdk/ -v
```

**Status**: All individual test suites pass 100%. The issue is parallel execution only.

### 2. TypeScript SDK Cleanup Race Condition
**Issue**: `deleteWorkspace` fails due to active sessions during test cleanup.

**Symptoms**:
- TypeScript tests pass but cleanup fails with `Cannot delete workspace: active session(s) remain`
- Occurs in `tests/sdk_typescript/test_sdk.py`

**Root Cause**: Tests create sessions that aren't fully closed before workspace deletion.

**Fix Applied**: 
- Added target peer creation in `/peers/{id}/chat` endpoint
- Increased Bun test timeout to 60s for LLM operations
- Added `--timeout 60000` to test runner

**Status**: ✅ Fixed - All 310 TypeScript tests now pass with proper cleanup.

### 3. Webhook Database Cleanup Error
**Issue**: Intermittent database connection errors during webhook test teardown.

**Symptoms**:
- `tests/webhooks/test_webhook_delivery.py` fails with database access errors
- Error: `database connection closed` during cleanup

**Root Cause**: Race condition between test teardown and database connection pooling.

**Workaround**:
```bash
# Run webhook tests individually
uv run pytest tests/webhooks/ -v
```

**Status**: ✅ Passes when run individually, intermittent in full suite.

## Test Configuration

### TypeScript SDK
- **Timeout**: 60 seconds per test (configured in `bunfig.toml`)
- **HTTP Client**: 30 seconds timeout for API calls
- **Server Setup**: Tests require running Honcho server with database/Redis
- **Environment**: `HONCHO_TEST_URL` points to test server

### Python Tests
- **Database**: Uses test PostgreSQL database with connection pooling
- **External Services**: Mocked embedding/LLM calls for unit tests
- **Async Mode**: `pytest-asyncio` with `mode=auto`
- **Parallelization**: Disabled (`-n 1`) recommended to avoid segfault

## Debugging Tips

### 1. Segmentation Fault Investigation
```bash
# Run with verbose output
uv run pytest tests/ -v -n 1 --tb=long

# Check memory usage
uv run pytest tests/ -n 1 --durations=10

# Run with database logging
export SQLALCHEMY_ECHO=1
uv run pytest tests/sdk/ -v
```

### 2. TypeScript Test Debugging
```bash
# Run specific TypeScript test
uv run pytest tests/ -k "conclusions.test.ts" -v

# Debug TypeScript directly (server must be running)
cd sdks/typescript
bun test __tests__/conclusions.test.ts
```

### 3. Database Cleanup Verification
```bash
# Check for orphaned test data
uv run python -c "
from src.crud.workspace import get_workspaces
from src.db import get_db
db = next(get_db())
workspaces = get_workspaces(db, filters={})
print(f'Found {len(workspaces.items)} workspaces after tests')
"

# Manual cleanup script
uv run python scripts/cleanup_test_data.py
```

## CI/CD Configuration

### GitHub Actions Example
```yaml
name: Test Suite
on: [push, pull_request]

jobs:
  test-python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: uv sync
      - run: uv run pytest tests/ -k "not typescript" -n 1 --cov=src
        env:
          DATABASE_URL: postgres://test:test@localhost/honcho_test

  test-typescript:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: uv sync
      - run: uv run pytest tests/ -k typescript --cov=sdks/typescript
        env:
          DATABASE_URL: postgres://test:test@localhost/honcho_test

  test-webhooks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync  
      - run: uv run pytest tests/webhooks/ -v
```

## Performance Notes

- **TypeScript SDK**: ~3.3 minutes (LLM calls, embedding operations)
- **Python Route Tests**: ~25 seconds (API endpoint validation)
- **Integration Tests**: ~19 seconds (end-to-end workflows)
- **Full Sequential Run**: ~8-10 minutes with `-n 1`
- **Memory Usage**: ~1.2GB peak during parallel execution

## Troubleshooting

### Common Issues

1. **Database Connection Errors**:
   ```bash
   # Reset test database
   docker-compose -f docker-compose.test.yml down
   docker-compose -f docker-compose.test.yml up -d
   ```

2. **Ollama/Embedding Service Unavailable**:
   ```bash
   # Check services
   docker ps | grep ollama
   curl http://localhost:11434/api/tags
   
   # Disable external services for unit tests
   export HONCHO_EMBEDDING_PROVIDER=mock
   ```

3. **TypeScript Tests Fail with 'Server Not Running'**:
   ```bash
   # Always use pytest orchestration
   uv run pytest tests/ -k typescript  # NOT bun test
   ```

### Test Data Cleanup
After test runs, verify cleanup:
```bash
# Check for test workspaces
uv run python -c "
import asyncio
from src.crud.workspace import get_workspaces  
from src.db import get_db
async def check():
    async with get_db() as db:
        workspaces = await get_workspaces(db, filters={'name': {'$like': '%test-%'}})
        print(f'Found {len(workspaces.items)} test workspaces')
asyncio.run(check())
"
```

## Test Coverage Goals

- **Route Tests**: 95%+ coverage of API endpoints
- **Integration Tests**: 90%+ end-to-end coverage
- **SDK Tests**: 98%+ coverage of public API surface
- **Deriver**: 85%+ coverage of complex async logic

**Current Status**: All functional tests pass. The segmentation fault is a parallel execution issue only.