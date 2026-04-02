#!/bin/bash
# Run tests with proper configuration for embedding provider
# Usage: ./scripts/run-tests.sh [options] [pytest_args]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Load .env.test if it exists
if [ -f .env.test.local ]; then
    echo "Loading .env.test.local..."
    export $(grep -v '^#' .env.test.local | xargs)
elif [ -f .env.test ]; then
    echo "Loading .env.test..."
    export $(grep -v '^#' .env.test | xargs)
fi

# Default values
USE_OLLAMA=""
PARALLEL="auto"
MARKERS=""
RUN_SEQUENTIAL_WITH_FORK=true

# Parse arguments
PYTEST_ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --sequential-only)
            PARALLEL="0"
            MARKERS="-m sequential"
            RUN_SEQUENTIAL_WITH_FORK=true
            shift
            ;;
        --no-parallel)
            PARALLEL="0"
            shift
            ;;
        --no-fork)
            RUN_SEQUENTIAL_WITH_FORK=false
            shift
            ;;
        --with-ollama)
            USE_OLLAMA=1
            shift
            ;;
        --no-embedding)
            MARKERS="-k 'not embedding'"
            shift
            ;;
        --help)
            echo "Usage: $0 [options] [pytest_args]"
            echo ""
            echo "Options:"
            echo "  --sequential-only    Run only sequential tests (no parallelization, with fork)"
            echo "  --no-parallel        Run all tests sequentially"
            echo "  --no-fork            Disable process forking for sequential tests"
            echo "  --with-ollama        Use real Ollama for embeddings (slower)"
            echo "  --no-embedding       Skip tests requiring embeddings"
            echo "  --help               Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  HONCHO_TEST_USE_OLLAMA=1     Use real Ollama embeddings"
            echo ""
            echo "Examples:"
            echo "  $0                                  # Default: mocked embeddings (fast)"
            echo "  $0 --with-ollama                    # Use real Ollama (slower)"
            echo "  $0 --sequential-only                # Run sequential tests only"
            echo "  $0 tests/routes/test_messages.py   # Run specific file"
            exit 0
            ;;
        *)
            PYTEST_ARGS+=("$1")
            shift
            ;;
    esac
done

# Apply Ollama setting
if [ "$USE_OLLAMA" = "1" ]; then
    export HONCHO_TEST_USE_OLLAMA=1
    echo "✓ Using real Ollama for embeddings"
    
    # Check if Ollama is available
    OLLAMA_URL="${HONCHO_LLM__OLLAMA_BASE_URL:-http://localhost:11434}"
    if ! curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "⚠️  Warning: Ollama not available at $OLLAMA_URL"
        echo "   Tests requiring embeddings may fail."
        echo "   To start Ollama: ollama serve &"
        echo "   To pull model: ollama pull nomic-embed-text:latest"
        echo ""
        exit 1
    else
        echo "✓ Ollama available at $OLLAMA_URL"
    fi
else
    echo "✓ Using mocked embeddings (fast, no Ollama required)"
fi

# Run tests differently based on mode
if [ "$PARALLEL" = "auto" ] && [ -z "$MARKERS" ]; then
    # Default mode: run parallel tests first (excluding sequential), then sequential with fork
    echo "Running parallel tests (excluding sequential)..."
    echo ""
    
    # Run non-sequential tests in parallel
    uv run pytest tests/ \
        -n auto \
        -m "not sequential" \
        --tb=short \
        -q \
        "${PYTEST_ARGS[@]}" || { echo "Parallel tests failed"; exit 1; }
    
    echo ""
    echo "Running sequential tests with process forking..."
    echo ""
    
    # Run sequential tests with --forked for isolation
    if [ "$RUN_SEQUENTIAL_WITH_FORK" = true ]; then
        uv run pytest tests/ \
            -m sequential \
            --forked \
            --tb=short \
            -q \
            "${PYTEST_ARGS[@]}" || { echo "Sequential tests failed"; exit 1; }
    else
        uv run pytest tests/ \
            -m sequential \
            --tb=short \
            -q \
            "${PYTEST_ARGS[@]}" || { echo "Sequential tests failed"; exit 1; }
    fi
else
    # Use requested configuration
    echo "Running tests..."
    echo "  Parallel workers: $PARALLEL"
    if [ -n "$MARKERS" ]; then
        echo "  Markers: $MARKERS"
    fi
    if [ ${#PYTEST_ARGS[@]} -gt 0 ]; then
        echo "  Pytest args: ${PYTEST_ARGS[*]}"
    fi
    echo ""
    
    # Build pytest command
    CMD="uv run pytest tests/ -n $PARALLEL"
    if [ -n "$MARKERS" ]; then
        CMD="$CMD $MARKERS"
    fi
    if [ "$PARALLEL" = "0" ] && [ "$RUN_SEQUENTIAL_WITH_FORK" = true ]; then
        CMD="$CMD --forked"
    fi
    
    $CMD "${PYTEST_ARGS[@]}"
fi

echo ""
echo "✓ All tests passed!"