# Local Honcho User Guide

A step-by-step guide for installing and using the Honcho Pi local memory server.

---

> **Two deployment modes.** This guide covers both:
> - **`honcho-pi` CLI installer** — a self-contained installer that manages its own
>   config at `~/.config/honcho-pi/` (Docker or existing Postgres).
> - **The actual running services** in this repo — systemd user services
>   (`honcho-api`, `honcho-deriver`) running from `~/.local/lib/honcho/`, configured
>   via `config.toml` + secrets-only `.env`. **When the sections below say
>   `~/.config/honcho-pi/.env`, that applies only to the `honcho-pi` CLI path**;
>   the running services use `~/.local/lib/honcho/config.toml` (see Configuration Reference).


## Features

The Local Honcho deployment adds the following features beyond upstream Honcho:

### Memory pipeline
- **Deriver** (background worker) processes incoming messages into observations/conclusions for each peer.
- **Dreamer** consolidates memory in the background — deduction, induction, synthesis, and contradiction detection — producing `deductive`, `inductive`, `synthesis`, and `contradiction`-level conclusions with provenance (`source_ids`, `times_derived`).
- **Dialectic (chat) API** (`/peers/{peer_id}/chat`) answers with context retrieved from memory; supports reasoning levels `minimal/low/medium/high/max`.

### Conclusions (derived knowledge store)
- Conclusions are Honcho's unit of memory: `explicit` (extracted from messages), `deductive`/`inductive`/`synthesis`/`contradiction` (derived while dreaming).
- Full read/write/delete + semantic search + reasoning-tree traversal (MCP tools: `list_conclusions`, `query_conclusions`, `get_conclusions`, `get_derived_conclusions`, `create_conclusions`, `delete_conclusion`).

### LLM rate-limit deferral (429 handling)
- A provider HTTP 429 is treated as a **temporary throttle**, not a failure: the deriver defers the affected queue record (leaves it unprocessed/un-errored), records a `rate_limit_since` stamp, and moves on to other records — so the queue keeps draining while the provider recovers.
- **Claim-gate**: a rate-limited work unit is not re-claimed until its cooldown elapses (prevents busy-looping against a throttled provider).
- Config knobs: `LLM_RETRY_RATELIMIT_*` (enabled, quick retries, delay, cooldown, max deferrals, metric).

### Queue scheduling
- **Weighted Round-Robin (WRR)** scheduling (`[deriver.wrr]`) prevents starvation of low-volume task types (webhook, dream, reconciler); configurable weights/min/max slots and idle-fill strategy.

### MCP server
- A full Model Context Protocol server (`mcp/`, v3) exposes **40 tools** over workspace/peer/session/scope/conclusion/system categories (stdio, HTTP, and streamable-http/Cloudflare Workers). Remote: `https://mcp.honcho.dev`.

### Config model
- **`config.toml`** holds all non-secret configuration; **`.env`** holds secrets only. `api_key_env` references in TOML point at secret env vars.

### Durable session notifications
- Durable sessions produce **OS-level desktop notifications** (`notify-send`) on completion. Telegram/Discord/Luna external messaging are disabled by policy.


## Quick Start

### 1. Download & Install

**Linux x86_64:**
```bash
curl -sL https://github.com/dsidlo/honcho/releases/latest/download/honcho-pi-linux-x86_64.tar.gz | \
  tar xz -C /tmp/
sudo mv /tmp/honcho-pi /usr/local/bin/
honcho-pi --version
```

**Linux ARM64 (Raspberry Pi, etc.):**
```bash
curl -sL https://github.com/dsidlo/honcho/releases/latest/download/honcho-pi-linux-aarch64.tar.gz | \
  tar xz -C /tmp/
sudo mv /tmp/honcho-pi /usr/local/bin/
```

### 2. Install Honcho Services

```bash
honcho-pi install
```

This interactive wizard will:
- Set up PostgreSQL (Docker or existing)
- Configure LLM providers
- Install Pi extension hooks
- Create systemd services

### 3. Start Services

```bash
honcho-pi start
```

### 4. Verify Installation

```bash
honcho-pi self status
```

Expected output:
```
✓ Honcho Pi Status
════════════════════
Version:     1.0.0
Config dir:  ~/.config/honcho-pi
Data dir:    ~/.local/share/honcho-pi

✓ Services
────────────────────
API:         Running (PID: 1234)
Deriver:     Running (PID: 1235)

✓ Configuration
────────────────────
API URL:     http://localhost:8333
Database:    postgresql+psycopg://honcho@localhost:5432/honcho
Workspace:   default

✓ Pi Integration
────────────────────
Extension:   Loaded (hooks: 12)
Obs Mode:    session
Git Tracking: enabled

✓ Health Checks
────────────────────
API:         healthy
Database:    connected
```

---

## Detailed Installation

### Prerequisites

- Linux x86_64 or ARM64
- systemd (for service management)
- Docker (optional, for PostgreSQL)
- At least 2GB RAM, 10GB disk

### PostgreSQL Options

**Option A: Docker (Recommended for quick start)**
```bash
honcho-pi install
# > Choose "Docker" for database
# Creates: honcho-pi-db container
```

**Option B: Existing PostgreSQL**
```bash
honcho-pi install
# > Choose "Existing" for database
# Enter: postgresql+psycopg://user:pass@host:5432/dbname
```

**Option C: Manual Setup**
```bash
# Install PostgreSQL with pgvector
sudo apt install postgresql-16 postgresql-16-pgvector

# Create database
sudo -u postgres createdb honcho
sudo -u postgres createuser honcho

# Configure in ~/.config/honcho-pi/.env
HONCHO_DB_CONNECTION_URI=postgresql+psycopg://honcho:password@localhost:5432/honcho
```

### LLM Provider Configuration

During `honcho-pi install`, you'll be prompted for LLM credentials:

**Anthropic Claude (Recommended)**
```
Provider: anthropic
API Key: sk-ant-xxxxx
Model: claude-sonnet-4-20250514
```

**OpenAI**
```
Provider: openai
API Key: sk-xxxxxxxxxxxxx
Model: gpt-4
```

**Self-Hosted (vLLM/Ollama)**
```
Provider: vllm
Base URL: http://localhost:11434/v1
Model: mixtral
```

---

## Daily Usage

### Starting/Stopping Services

```bash
# Start all services
honcho-pi start

# Stop all services
honcho-pi stop

# Restart (useful after config changes)
honcho-pi restart

# View logs
honcho-pi logs -f

# API only
honcho-pi start api

# Deriver only
honcho-pi start deriver
```

### Checking Status

```bash
# Human-readable status
honcho-pi self status

# Machine-readable JSON (for scripts)
honcho-pi self status --json
```

### Reconfiguration

Change settings without reinstalling:

```bash
# Interactive reconfiguration
honcho-pi self configure

# Or edit config directly
nano ~/.config/honcho-pi/.env
honcho-pi restart
```

### Diagnostics

When things aren't working:

```bash
# Run all diagnostic checks
honcho-pi self doctor

# Auto-fix where possible
honcho-pi self doctor --fix

# Check specific areas
honcho-pi self doctor --component api
honcho-pi self doctor --component database
honcho-pi self doctor --component pi
```

---

## Configuration Reference

The current deployment splits configuration into two files (the older single `.env`-everything approach was migrated):

- **`config.toml`** (in `~/.local/lib/honcho/`) — all non-secret configuration: app, DB, embedding, deriver (incl. WRR), dialectic levels, summary, dream.
- **`.env`** (in `~/.local/lib/honcho/`) — **secrets only** (API keys, DB connection URIs). Referenced from `config.toml` via `api_key_env`.

> Precedence: `init > env > .env > config.toml > secrets > defaults`. A value set in `.env`/environment still overrides `config.toml`.

**Core Settings (`config.toml`)**
```toml
[app]
honcho_port = 8300                        # API port
allowed_ip_networks = "127.0.0.0/8,172.20.0.0/24"

[db]
schema = "public"
pool_size = 10
max_overflow = 20
pool_timeout = 30

[embedding.model_config]
model = "qwen3-embedding:8b"
transport = "openai"
[embedding.model_config.overrides]
base_url = "http://localhost:11434/v1"
api_key_env = "OLLAMA_API_KEY"    # secret lives in .env
```

**Secrets (`.env`)**
```bash
OLLAMA_API_KEY=...
LLM_VLLM_API_KEY=...
INCEPTIONLABS_API_KEY=...
HONCHO_DB_CONNECTION_URI=postgresql+psycopg://dsidlo@localhost:5433/honcho
DB_CONNECTION_URI=postgresql+psycopg://dsidlo@localhost:5433/honcho
```

**Deriver (Memory Processing) — `config.toml`**
```toml
[deriver]
workers = 8
flush_enabled = true

[deriver.model_config]
model = "mercury-3"
transport = "openai"
structured_output_mode = "json_object"
thinking_budget_tokens = 2048
max_output_tokens = 4096
[deriver.model_config.overrides]
base_url = "https://api.inceptionlabs.ai/v1"
api_key_env = "INCEPTIONLABS_API_KEY"
[deriver.model_config.fallback]
model = "qwen3.5:latest"
transport = "openai"
[deriver.model_config.fallback.overrides]
base_url = "http://localhost:11434/v1"
api_key_env = "LLM_VLLM_API_KEY"
```

**Dialectic (Chat/Query API) — `config.toml`**
```toml
[dialectic.levels.medium.model_config]
model = "glm-5.3-flash:cloud"
transport = "openai"
max_tool_iterations = 5
thinking_budget_tokens = 1024
[dialectic.levels.medium.model_config.overrides]
base_url = "https://ollama.com/v1"
api_key_env = "LLM_VLLM_API_KEY"
```

**Dreamer (Memory Synthesis) — `config.toml`**
```toml
[dream]
document_threshold = 25
idle_timeout_minutes = 30
min_hours_between_dreams = 4

[dream.deduction_model_config]
model = "glm-5.3-flash:cloud"
transport = "openai"
[dream.deduction_model_config.overrides]
base_url = "https://ollama.com/v1"
api_key_env = "LLM_VLLM_API_KEY"
```

**Pi Integration (extension config — `~/.pi/settings.json`)**
```json
{
  "extension.honcho": {
    "enabled": true,
    "baseUrl": "http://localhost:8300",
    "workspace": "default",
    "agentId": "agent-pi-mono",
    "observationMode": "session",
    "includeThoughts": true,
    "includeTools": true,
    "includeResults": true,
    "gitTracking": true
  }
}
```

### Configuration Files Location

```
~/.local/lib/honcho/
├── config.toml              # all non-secret configuration
├── .env                     # secrets only
└── .env.honcho.bk           # backup of pre-migration .env
```

## Troubleshooting

### Services Won't Start

```bash
# Check service logs
journalctl --user -u honcho-api -n 50
journalctl --user -u honcho-deriver -n 50

# Validate configuration
honcho-pi self doctor --component config

# Reset services
honcho-pi stop
rm -rf ~/.config/systemd/user/honcho-*.service
honcho-pi self configure
honcho-pi install --force
```

### Database Connection Failed

```bash
# Test connection manually
psql "postgresql+psycopg://honcho:password@localhost:5432/honcho"

# Check PostgreSQL status
sudo systemctl status postgresql
# or
docker ps | grep honcho-pi-db

# Reset database (WARNING: destructive)
honcho-pi self reset-db
```

### Pi Extension Not Working

```bash
# Check extension loaded
ls -la ~/.pi/agent/extensions/honcho.ts

# Verify Pi settings
cat ~/.pi/settings.json | jq '.["extension.honcho"]'

# Reinstall extension
honcho-pi self configure --component pi

# Restart Pi
# In Pi TUI, press Ctrl+Shift+R to reload extensions
```

### Out of Memory

```bash
# Check memory usage
honcho-pi self status --json | jq .resources.memory

# Reduce deriver workers
# Edit ~/.config/honcho-pi/.env:
DERIVER_WORKERS=1

# Restart
honcho-pi restart
```

### Update Issues

```bash
# Check current version
honcho-pi --version

# Manual update if self-update fails
curl -sL ... | tar xz -C /tmp/
sudo mv /tmp/honcho-pi /usr/local/bin/

# Validate after update
honcho-pi self doctor
```

---

## Advanced Usage

### Working with Multiple Workspaces

```bash
# Create workspace
honcho-pi --workspace project1 self status

# Or set in config
echo 'HONCHO_WORKSPACE=project1' >> ~/.config/honcho-pi/.env
```

### Custom Service Templates

Override default templates:

```bash
mkdir -p ~/.config/honcho-pi/templates
cp /usr/local/share/honcho-pi/templates/*.service ~/.config/honcho-pi/templates/
# Edit templates
honcho-pi self configure --component services
```

### Manual Database Migrations

```bash
# From honcho source directory
cd ~/.local/share/honcho-pi/honcho_src/
uv run alembic upgrade head
```

### Running Multiple Instances

```bash
# Instance 1: default
honcho-pi start

# Instance 2: separate config
honcho-pi --config-dir ~/.config/honcho-pi-prod --data-dir ~/.local/share/honcho-pi-prod start
```

### Integration with Pi Config

Add to `~/.pi/settings.json`:

```json
{
  "extension.honcho": {
    "enabled": true,
    "baseUrl": "http://localhost:8333",
    "workspace": "default",
    "agentId": "agent-pi-mono",
    "observationMode": "session",
    "includeThoughts": true,
    "includeTools": true,
    "includeResults": true,
    "gitTracking": true,
    "agentName": "Honcho"
  }
}
```

---

## Uninstallation

```bash
# Stop services
honcho-pi stop

# Remove binary
sudo rm /usr/local/bin/honcho-pi

# Remove data (optional - destructive!)
rm -rf ~/.local/share/honcho-pi
rm -rf ~/.config/honcho-pi

# Remove database (if Docker)
docker rm -f honcho-pi-db

# Remove Pi extension
rm ~/.pi/agent/extensions/honcho.ts
```

---

## Best Practices

1. **Backup before major changes**
   ```bash
   cp -r ~/.config/honcho-pi ~/.config/honcho-pi.backup
   ```

2. **Use reasoning levels appropriately**
   - `minimal`: Quick queries, low latency
   - `medium`: General chat (default)
   - `high/max`: Deep analysis, synthesis

3. **Monitor deriver queue**
   ```bash
   honcho-pi self status | grep -A 5 "Deriver Queue"
   ```

4. **Keep API keys secure**
   ```bash
   chmod 600 ~/.config/honcho-pi/.env
   ```

5. **Regular updates**
   ```bash
   honcho-pi self update
   ```

---

## Getting Help

- **Documentation**: https://docs.honcho.dev
- **GitHub**: https://github.com/dsidlo/honcho
- **Issues**: https://github.com/dsidlo/honcho/issues
- **Discord**: [Community Link]

---

## Command Quick Reference

| Command | Description |
|---------|-------------|
| `honcho-pi install` | First-run configuration |
| `honcho-pi start` | Start services |
| `honcho-pi stop` | Stop services |
| `honcho-pi restart` | Restart services |
| `honcho-pi logs -f` | View logs |
| `honcho-pi self status` | Show status |
| `honcho-pi self configure` | Reconfigure |
| `honcho-pi self doctor` | Diagnostics |
| `honcho-pi self update` | Update binary |
| `honcho-pi --version` | Show version |
| `honcho-pi --help` | Show help |

---

**Version**: 1.0  
**Last Updated**: 2026-04-09
