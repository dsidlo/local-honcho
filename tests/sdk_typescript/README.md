# TypeScript SDK Integration Tests

This directory contains integration tests for the TypeScript SDK.

## Requirements

These tests require additional infrastructure to be set up:

### 1. bun (JavaScript Runtime)

The tests use `bun` to run the TypeScript SDK test suite.

**Installation:**

```bash
# Using the official installer
curl -fsSL https://bun.sh/install | bash

# Or via npm
npm install -g bun

# Or via Homebrew (macOS)
brew install oven-sh/bun/bun
```

**Verify installation:**
```bash
bun --version  # Should print 1.x.x
```

### 2. TypeScript SDK Dependencies

The TypeScript SDK must have its dependencies installed:

```bash
cd /home/dsidlo/workspace/honcho/sdks/typescript
bun install
```

This creates `node_modules/` with all required dependencies.

## How the Tests Work

The tests use a specialized setup:

1. **ts_test_server fixture** (`conftest.py`):
   - Starts a real Uvicorn HTTP server in a background thread
   - Uses the test database (same as Python tests)
   - Runs on a random available port
   - Patches `tracked_db` to create fresh sessions for concurrent requests

2. **test_typescript_sdk** (`test_sdk.py`):
   - Uses the running server URL from the fixture
   - Runs `bun test` in the TypeScript SDK directory via subprocess
   - Sets `HONCHO_TEST_URL` environment variable pointing to the test server
   - Fails if the TypeScript tests fail

3. **test_typescript_sdk_typecheck** (`test_sdk.py`):
   - Runs `bun run typecheck` to verify TypeScript types
   - Ensures SDK types are consistent with usage patterns

## Running the Tests

```bash
# From the project root
cd /home/dsidlo/workspace/honcho

# Run just TypeScript SDK tests (requires bun and bun install)
uv run pytest tests/sdk_typescript/ -v

# Skip TypeScript tests (if you don't have bun installed)
uv run pytest tests/ --ignore=tests/sdk_typescript
```

## Troubleshooting

### "bun: command not found"

Install bun first (see Requirements section above).

### "node_modules not found" or import errors

Run `bun install` in the TypeScript SDK directory:

```bash
cd sdks/typescript
bun install
cd ../..
```

### Server startup timeout

The `ts_test_server` fixture waits up to 10 seconds for the server to start. If your system is slow, you may need to increase this timeout in `conftest.py`.

### Port conflicts

The tests use a random free port (found via `find_free_port()`). If you have port conflicts, the test will fail with a clear error.

## Why These Tests Exist

These tests ensure that:
1. The TypeScript SDK works correctly with the current server
2. API changes don't break SDK functionality
3. TypeScript types remain accurate
4. End-to-end integration works as expected

Unlike the Python tests which use `TestClient` (in-process), these tests run a real HTTP server and use the actual TypeScript SDK over HTTP, catching issues that wouldn't appear in mocked tests.
