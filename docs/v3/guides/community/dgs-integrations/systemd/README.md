# Honcho Systemd Services

Systemd service configurations for running Honcho API and Deriver as user services.

## Directory Structure

```
~
├── .config/systemd/user/
│   ├── honcho-api.service      # Honcho API server service
│   └── honcho-deriver.service  # Honcho Deriver background worker service
├── .local/lib/honcho/          # Honcho installation directory
│   ├── .venv/                  # Python virtual environment
│   └── src/                    # Source code
└── .env                        # Environment configuration
```

## Installation

1. Copy service files to your systemd user directory:
```bash
mkdir -p ~/.config/systemd/user/
cp .config/systemd/user/*.service ~/.config/systemd/user/
```

2. Install Honcho to `~/.local/lib/honcho/`:
```bash
# Clone or copy Honcho source
git clone <honcho-repo> ~/.local/lib/honcho
cd ~/.local/lib/honcho

# Create virtual environment and install dependencies
uv sync
```

3. Copy and configure environment variables:
```bash
cp .env.template ~/.env
# Edit ~/.env with your configuration
```

4. Enable and start services:
```bash
# Reload systemd daemon
systemctl --user daemon-reload

# Start and enable API service
systemctl --user enable --now honcho-api.service

# Start and enable Deriver service  
systemctl --user enable --now honcho-deriver.service
```

## Service Management

### Check service status
```bash
systemctl --user status honcho-api
systemctl --user status honcho-deriver
```

### View logs
```bash
# Follow logs in real-time
journalctl --user -u honcho-api -f
journalctl --user -u honcho-deriver -f

# View last 100 lines
journalctl --user -u honcho-api --no-pager -n 100
```

### Restart services
```bash
systemctl --user restart honcho-api
systemctl --user restart honcho-deriver
```

### Stop services
```bash
systemctl --user stop honcho-api
systemctl --user stop honcho-deriver
```

## Configuration

### honcho-api.service
- **WorkingDirectory**: Where Honcho is installed (~/.local/lib/honcho)
- **ExecStart**: `uv run fastapi dev src/main.py`
- **Port**: 8000 (configurable via APP_PORT in .env)
- **Restart**: Always with 10s delay

### honcho-deriver.service
- **WorkingDirectory**: Where Honcho is installed (~/.local/lib/honcho)
- **ExecStart**: `uv run python -m src.deriver`
- **EnvironmentFile**: Points to ~/.env for configuration
- **Restart**: Always with 10s delay

## Environment Variables

Key variables in `~/.env`:

```ini
# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5433/honcho

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM Provider
LLM_PROVIDER=vllm
VLLM_API_KEY=your-api-key
VLLM_BASE_URL=https://api.example.com/v1

# Embedding Provider
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=bge-m3
MAX_EMBEDDING_TOKENS=8192

# Logging
LOG_LEVEL=DEBUG
```

## Troubleshooting

### Service fails to start
Check logs: `journalctl --user -u honcho-api --no-pager`

### Port already in use
Edit `~/.env` and change `APP_PORT`, then restart:
```bash
systemctl --user restart honcho-api
```

### Environment variables not loading
1. Check `~/.env` exists and has correct values
2. Verify `EnvironmentFile` path in service file
3. Run `systemctl --user daemon-reload` after changes

### Permission issues
Ensure the user has proper permissions:
```bash
ls -la ~/.local/lib/honcho/
ls -la ~/.config/systemd/user/
```

## Files

- `.config/systemd/user/honcho-api.service` - API server systemd service
- `.config/systemd/user/honcho-deriver.service` - Deriver worker systemd service  
- `.env.template` - Environment variable template
