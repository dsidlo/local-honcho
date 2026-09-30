"""
TypeScript SDK Integration Tests

Runs the TypeScript SDK test suite against a real Honcho server.
This ensures SDK changes don't break when server code changes.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Path to the TypeScript SDK
SDK_PATH = Path(__file__).parent.parent.parent / "sdks" / "typescript"


def _require_bun() -> str:
    """Resolve the bun binary or fail fast with install instructions.

    Without this check, failing subprocess.run (FileNotFoundError) only
    surfaces after the server fixture boots; and a bun that exists but is
    not on subprocess's PATH silently hangs the test for the full timeout.
    """
    result = shutil.which("bun")
    if result is None:
        pytest.fail(
            "'bun' not found on PATH. Install it (https://bun.sh) or add "
            "~/.bun/bin to PATH to run TypeScript SDK tests."
        )
    return result


def test_typescript_sdk(ts_test_server: str):
    """
    Run the TypeScript SDK tests against the test server.

    This test:
    1. Uses the ts_test_server fixture which starts a real HTTP server
    2. Passes the server URL to the TypeScript tests via environment variable
    3. Runs `bun test` and captures output
    4. Fails if any TypeScript tests fail
    """
    _require_bun()
    env = {
        **os.environ,
        "HONCHO_TEST_URL": ts_test_server,
        # Disable retries for faster test failures
        "HONCHO_MAX_RETRIES": "0",
    }

    result = subprocess.run(
        ["bun", "test", "--timeout", "60000"],
        cwd=str(SDK_PATH),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,  # 10 minute timeout (310 tests incl. real LLM calls)
    )

    # Print output for debugging
    if result.stdout:
        print("\n=== TypeScript SDK Test Output ===")
        print(result.stdout)

    if result.returncode != 0:
        print("\n=== TypeScript SDK Test Errors ===")
        print(result.stderr)
        pytest.fail(f"TypeScript SDK tests failed with exit code {result.returncode}")


def test_typescript_sdk_typecheck():
    """
    Run TypeScript type checking on the SDK.

    This ensures the SDK's types are consistent with usage patterns.
    """
    _require_bun()
    result = subprocess.run(
        ["bun", "run", "typecheck"],
        cwd=str(SDK_PATH),
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        print("\n=== TypeScript Type Errors ===")
        print(result.stdout)
        print(result.stderr)
        pytest.fail(f"TypeScript type check failed with exit code {result.returncode}")
