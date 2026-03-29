# Honcho systemd Service Configuration

This document defines requirements for running Honcho (API server and deriver) as systemd user services.

---

## Overview

Running Honcho as systemd services provides:
- Automatic startup on boot/login
- Automatic restart on failure
- Centralized logging via journald
- Controlled shutdown/restart semantics
- Resource management

---

## Prerequisites

### 1. System Requirements

| Component | Version/Requirement |
|-----------|-------------------|
| systemd | v240+ (user services support) |
| PostgreSQL | 15+ with pgvector extension |
| Python | 3.11+ with virtual environment |
| uv | Latest (for running commands) |
| User permissions | Standard user (no root required for user services) |

### 2. Environment Requirements

The following must be configured before services can start:

```bash
# Required environment file at ~/.env
HONCHO_BASE_URL=http://localhost:8000
HONCHO_WORKSPACE=default
HONCHO_USER=<user>
HONCHO_AGENT_ID=agent-pi-mono
HONCHO_WORKSPACE_MODE=auto

# Database
DB_CONNECTION_URI=postgresql+psycopg://<user>@localhost:5433/postgres

# LLM Configuration
LLM_VLLM_BASE_URL=http://localhost:11434/v1
LLM_VLLM_API_KEY=ollama
LLM_EMBEDDING_PROVIDER=ollama
LLM_OLLAMA_BASE_URL=http://localhost:11434
LLM_OLLAMA_EMBEDDING_MODEL=nomic-embed-text:latest

# All other DERIVER, DIALECTIC, DREAM settings from honcho.ts extension
```

### 3. Directory Structure

Honcho source must be installed at the configured location:

```
~/.local/lib/honcho/
├── src/
│   ├── main.py              # API entry point
│   ├── deriver/             # Background worker
│   └── ...
├── pyproject.toml           # Python dependencies
├── uv.lock                  # Locked dependencies
└── .venv/                   # Virtual environment
    └── bin/
        ├── python           # Python interpreter
        └── uv               # uv package manager
```

---

## Service Definitions

### Service 1: honcho-api.service

**Purpose**: Run the FastAPI HTTP server

**Unit File Location**: `~/.config/systemd/user/honcho-api.service`

**Requirements**:
- Type: `exec` or `simple`
- Working directory: `~/.local/lib/honcho`
- Environment file: `~/.env`
- Port binding: 0.0.0.0:8000 (configurable)
- Dependencies: PostgreSQL, Redis (if using cache)

**Service Specifications**:

```ini
[Unit]
Description=Honcho API Server
Documentation=https://docs.honcho.dev
# Wait for network and postgresql
After=network-online.target
Wants=network-online.target

[Service]
# Service type
Type=exec

# Working directory must contain pyproject.toml and src/
WorkingDirectory=%h/.local/lib/honcho

# Command to start API server
# Option A: Using uv run (recommended)
ExecStart=%h/.local/lib/honcho/.venv/bin/uv run --no-dev fastapi dev src/main.py --host 0.0.0.0 --port 8000

# Option B: Using python directly (faster startup)
# ExecStart=%h/.local/lib/honcho/.venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# Environment
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=%h/.env

# Process management
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3

# Resource limits (optional)
# MemoryMax=512M
# CPUQuota=50%

# Graceful shutdown
TimeoutStopSec=30
KillSignal=SIGTERM

[Install]
# Start as part of default user session
WantedBy=default.target
```

**Key Configuration Notes**:
- `%h` expands to user's home directory
- `Type=exec` waits for process to fully start before considering service active
- `--no-dev` skips dev dependencies for faster startup
- `PYTHONUNBUFFERED=1` ensures logs appear immediately

---

### Service 2: honcho-deriver.service

**Purpose**: Run background deriver worker for processing messages

**Unit File Location**: `~/.config/systemd/user/honcho-deriver.service`

**Requirements**:
- Type: `simple` (runs indefinitely)
- Working directory: `~/.local/lib/honcho`
- Environment file: `~/.env`
- No port binding required
- Dependencies: PostgreSQL, Redis (if enabled)

**Service Specifications**:

```ini
[Unit]
Description=Honcho Deriver (Background Worker)
Documentation=https://docs.honcho.dev
After=network-online.target honcho-api.service
Wants=network-online.target

[Service]
# Service type
Type=simple

# Working directory
WorkingDirectory=%h/.local/lib/honcho

# Command to start deriver
# Option A: Using uv run
ExecStart=%h/.local/lib/honcho/.venv/bin/uv run --no-dev python -m src.deriver

# Option B: Direct python
# ExecStart=%h/.local/lib/honcho/.venv/bin/python -m src.deriver

# Environment
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=%h/.env

# Process management
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# Resource limits (optional)
# MemoryMax=256M

# Graceful shutdown
TimeoutStopSec=30
KillSignal=SIGTERM

[Install]
WantedBy=default.target
```

**Key Configuration Notes**:
- `Type=simple` appropriate for long-running processes
- `Restart=always` ensures deriver restarts even on clean exit
- Longer `RestartSec` (10s) to prevent tight restart loops

---

## Installation Procedure

### Step 1: Create User Service Directory

```bash
mkdir -p ~/.config/systemd/user
```

### Step 2: Install Service Files

Create two files:
- `~/.config/systemd/user/honcho-api.service`
- `~/.config/systemd/user/honcho-deriver.service`

(Contents defined in sections above)

### Step 3: Reload systemd

```bash
systemctl --user daemon-reload
```

### Step 4: Enable Services (auto-start)

```bash
systemctl --user enable honcho-api.service
systemctl --user enable honcho-deriver.service
```

### Step 5: Start Services

```bash
systemctl --user start honcho-api.service
systemctl --user start honcho-deriver.service
```

### Step 6: Verify Status

```bash
systemctl --user status honcho-api.service
systemctl --user status honcho-deriver.service
```

---

## Daily Management Commands

### Check Service Status

```bash
# Individual services
systemctl --user status honcho-api
systemctl --user status honcho-deriver

# All user services
systemctl --user --type=service
```

### View Logs

```bash
# Follow API logs
journalctl --user -u honcho-api -f

# Follow deriver logs
journalctl --user -u honcho-deriver -f

# View last 50 lines
journalctl --user -u honcho-api -n 50

# View since last boot
journalctl --user -u honcho-api --since boot

# View with timestamp
journalctl --user -u honcho-api -o short-iso
```

### Restart Services

```bash
# Restart API
systemctl --user restart honcho-api

# Restart deriver
systemctl --user restart honcho-deriver

# Restart both
systemctl --user restart honcho-api honcho-deriver
```

### Stop Services

```bash
systemctl --user stop honcho-api
systemctl --user stop honcho-deriver
```

### Disable Auto-start

```bash
systemctl --user disable honcho-api
systemctl --user disable honcho-deriver
```

---

## Troubleshooting

### Service Won't Start

**Check logs:**
```bash
journalctl --user -u honcho-api -n 100 --no-pager
```

**Common issues:**

1. **Working directory incorrect**
   - Verify `WorkingDirectory` path exists
   - Check that `pyproject.toml` is in that directory

2. **Virtual environment missing or broken**
   - Test: `~/.local/lib/honcho/.venv/bin/python --version`
   - Re-create: `cd ~/.local/lib/honcho && uv sync`

3. **Environment file missing**
   - Verify `~/.env` exists and is readable
   - Check file permissions: `ls -la ~/.env`

4. **Port already in use**
   - Check: `lsof -i :8000`
   - Kill existing process or change port

5. **PostgreSQL not running**
   - Verify: `systemctl status postgresql`
   - Start: `sudo systemctl start postgresql`

### Service Starts Then Exits

**Check for immediate crashes:**
```bash
journalctl --user -u honcho-api --since "1 minute ago"
```

**Common causes:**
- Missing database extension: `CREATE EXTENSION IF NOT EXISTS vector;`
- Missing API keys in `.env`
- Database connection refused (wrong port/credentials)

### Slow Startup

**Switch from `uv run` to direct Python:**
```ini
# Instead of:
ExecStart=%h/.local/lib/honcho/.venv/bin/uv run --no-dev fastapi dev src/main.py

# Use:
ExecStart=%h/.local/lib/honcho/.venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

### Logs Not Appearing

**Enable verbose logging:**
```ini
[Service]
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
```

---

## Advanced Configuration

### Conditional Startup (Socket Activation)

Create `~/.config/systemd/user/honcho-api.socket`:

```ini
[Unit]
Description=Honcho API Socket

[Socket]
ListenStream=8000
Accept=false

[Install]
WantedBy=sockets.target
```

Then modify `honcho-api.service`:
```ini
[Unit]
Requires=honcho-api.socket
After=honcho-api.socket

[Service]
ExecStart=%h/.local/lib/honcho/.venv/bin/python -m uvicorn src.main:app --fd 0
```

### Dependency on PostgreSQL

If PostgreSQL runs as system service:

```ini
[Unit]
After=postgresql.service
Requires=postgresql.service
```

If PostgreSQL runs as user service:

```ini
[Unit]
After=postgresql-15.service
Requires=postgresql-15.service
```

### Resource Limits

```ini
[Service]
# Memory
MemoryMax=512M
MemorySwapMax=0

# CPU
CPUQuota=50%

# File descriptors
LimitNOFILE=65536

# Processes
TasksMax=50
```

### Notification on Failure

```ini
[Service]
ExecStopPost=/bin/sh -c 'if [ "$$EXIT_STATUS" != 0 ]; then notify-send "Honcho API Failed" "Check journalctl"; fi'
```

---

## Uninstallation

### Remove Services

```bash
# Stop services
systemctl --user stop honcho-api honcho-deriver

# Disable auto-start
systemctl --user disable honcho-api honcho-deriver

# Remove service files
rm ~/.config/systemd/user/honcho-api.service
rm ~/.config/systemd/user/honcho-deriver.service

# Reload systemd
systemctl --user daemon-reload
```

### Clean Up Logs (Optional)

```bash
journalctl --user --vacuum-time=1d
```

---

## References

- [systemd User Services](https://wiki.archlinux.org/title/Systemd/User)
- [systemd Service Units](https://www.freedesktop.org/software/systemd/man/systemd.service.html)
- [Honcho Documentation](https://docs.honcho.dev)
- [uv Documentation](https://docs.astral.sh/uv/)

---

## Appendix: Service File Templates

### Quick Install Script

```bash
#!/bin/bash
# save as: install-honcho-services.sh

set -e

HONCHO_DIR="${HONCHO_DIR:-$HOME/.local/lib/honcho}"
mkdir -p ~/.config/systemd/user

# Create honcho-api.service
cat > ~/.config/systemd/user/honcho-api.service << EOF
[Unit]
Description=Honcho API Server
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
WorkingDirectory=$HONCHO_DIR
ExecStart=$HONCHO_DIR/.venv/bin/uv run --no-dev fastapi dev src/main.py --host 0.0.0.0 --port 8000
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=%h/.env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Create honcho-deriver.service
cat > ~/.config/systemd/user/honcho-deriver.service << EOF
[Unit]
Description=Honcho Deriver (Background Worker)
After=network-online.target honcho-api.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HONCHO_DIR
ExecStart=$HONCHO_DIR/.venv/bin/uv run --no-dev python -m src.deriver
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=%h/.env
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable honcho-api honcho-deriver

echo "Services installed. Start with:"
echo "  systemctl --user start honcho-api honcho-deriver"
```

Make executable and run:
```bash
chmod +x install-honcho-services.sh
./install-honcho-services.sh
```
