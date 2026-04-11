#!/bin/bash
# =============================================================================
# Entrypoint for honcho-pi test container
# Sets up systemd user sessions and waits for dependent services
# =============================================================================

set -e

echo "=== Honcho Pi Test Container Entrypoint ==="

# --- Set up runtime directory for systemd user session ---
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# --- Set up DBus for systemd user session ---
if [ ! -S /run/dbus/system_bus_socket ]; then
    echo "Starting D-Bus system bus..."
    mkdir -p /run/dbus
    dbus-daemon --system --fork 2>/dev/null || true
fi

# --- Determine database host ---
DB_HOST="${POSTGRES_HOST:-postgres}"
DB_PORT="${POSTGRES_PORT:-5432}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
OLLAMA_HOST="${OLLAMA_HOST:-ollama}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

# --- Wait for PostgreSQL ---
echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
for i in $(seq 1 60); do
    if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U postgres 2>/dev/null; then
        echo "PostgreSQL is ready!"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: PostgreSQL did not become ready in 120 seconds"
        exit 1
    fi
    sleep 2
done

# --- Wait for Redis ---
echo "Waiting for Redis at ${REDIS_HOST}:${REDIS_PORT}..."
for i in $(seq 1 30); do
    if curl -sf "http://${REDIS_HOST}:${REDIS_PORT}" 2>/dev/null || \
       redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
        echo "Redis is ready!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "WARNING: Redis not ready, continuing anyway (not strictly required)"
    fi
    sleep 2
done

# --- Wait for Ollama ---
echo "Waiting for Ollama at ${OLLAMA_HOST}:${OLLAMA_PORT}..."
for i in $(seq 1 40); do
    if curl -sf "http://${OLLAMA_HOST}:${OLLAMA_PORT}/api/tags" 2>/dev/null; then
        echo "Ollama is ready!"
        break
    fi
    if [ "$i" -eq 40 ]; then
        echo "ERROR: Ollama did not become ready in 200 seconds"
        exit 1
    fi
    sleep 5
done

# --- Pull bge-m3 embedding model ---
echo "Pulling bge-m3 embedding model..."
for i in $(seq 1 3); do
    if curl -sf "http://${OLLAMA_HOST}:${OLLAMA_PORT}/api/pull" \
         -d '{"name":"bge-m3"}' 2>/dev/null; then
        echo "bge-m3 model pulled successfully!"
        break
    fi
    echo "Pull attempt $i failed, retrying..."
    sleep 10
done

# --- Create pgvector extension ---
echo "Creating pgvector extension in honcho database..."
PGPASSWORD="${POSTGRES_PASSWORD:-honcho_test_password}" psql -h "$DB_HOST" -p "$DB_PORT" \
    -U postgres -d honcho -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || \
    echo "WARNING: Could not create pgvector extension (may already exist)"

# --- Print environment info ---
echo ""
echo "=== Environment Ready ==="
echo "PostgreSQL: ${DB_HOST}:${DB_PORT}"
echo "Redis: ${REDIS_HOST}:${REDIS_PORT}"
echo "Ollama: ${OLLAMA_HOST}:${OLLAMA_PORT}"
echo "honcho-pi binary: $(which honcho-pi 2>/dev/null || echo 'not in PATH')"
echo "honcho-pi version: $(honcho-pi --version 2>/dev/null || echo 'unknown')"
echo ""

# --- Execute command ---
exec "$@"