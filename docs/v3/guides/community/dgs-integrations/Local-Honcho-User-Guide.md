# Local Honcho User Guide

A step-by-step guide for installing and using the Honcho Pi local memory server.

---

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

### Environment Variables

Edit `~/.config/honcho-pi/.env`:

**Core Settings**
```bash
HONCHO_PORT=8300                        # API port
HONCHO_DB_CONNECTION_URI=postgresql+psycopg://honcho:honcho@localhost:5432/honcho
NAMESPACE=honcho                        # Multi-tenant namespace
```

**LLM Providers**
```bash
LLM_PROVIDER=vllm                       # Primary provider
LLM_ANTHROPIC_API_KEY=sk-ant-xxxxx
LLM_OPENAI_API_KEY=sk-xxxxxxxxxxxxx
LLM_VLLM_BASE_URL=http://localhost:11434/v1
```

**Deriver (Memory Processing)**
```bash
DERIVER_ENABLED=true                    # Background memory processing
DERIVER_WORKERS=8                       # Number of worker processes
DERIVER_PROVIDER=vllm                   # Provider for deriver
DERIVER_MODEL=kimi-k2.5
DERIVER_DEDUPLICATE=true                # Prevent duplicate observations
```

**Dialectic (Chat/Query API)**
```bash
DIALECTIC_MAX_OUTPUT_TOKENS=8192
DIALECTIC_HISTORY_TOKEN_LIMIT=4096

# Reasoning levels
DIALECTIC_LEVELS__MEDIUM__PROVIDER=vllm
DIALECTIC_LEVELS__MEDIUM__MODEL=kimi-k2.5
DIALECTIC_LEVELS__MEDIUM__THINKING_BUDGET_TOKENS=1024
```

**Dreamer (Memory Synthesis)**
```bash
DREAM_ENABLED=true                      # Background synthesis
DREAM_PROVIDER=anthropic                # Provider for dreams
DREAM_MODEL=claude-sonnet-4-20250514
DREAM_DEDUCTION_MODEL=claude-haiku-4-5
DREAM_INDUCTION_MODEL=claude-haiku-4-5
DREAM_SYNTHESIS_MODEL=claude-haiku-4-5
```

**Pi Integration**
```bash
PI_EXTENSION_ENABLED=true               # Enable Pi hooks
PI_OBS_ENABLED=true                     # Observe conversations
PI_OBS_INCLUDE_THOUGHTS=true           # Include tool thoughts
PI_OBS_INCLUDE_TOOLS=true              # Include tool calls
PI_OBS_INCLUDE_RESULTS=true            # Include tool results
```

### Configuration Files Location

```
~/.config/honcho-pi/
├── .env                    # Main configuration
├── honcho.env              # Service environment (auto-generated)
└── systemd/user/
    ├── honcho-api.service
    └── honcho-deriver.service
```

---

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
