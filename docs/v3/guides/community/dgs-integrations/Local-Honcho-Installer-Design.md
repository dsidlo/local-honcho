# Local-Honcho-Installer-Design.md

## Overview

This document outlines the design and implementation steps for creating a self-extracting shell script installer for the Local Honcho memory service tailored for pi-mono users. The installer will package the Honcho backend (from `~/.local/lib/honcho/`), the pi-mono extension (`honcho.ts`), and supporting components (systemd services, Docker DB setup) into a single tarball wrapped in a bash script.

**Goals**:
- One-command install: `curl -sL https://github.com/dsidlo/honcho-pi/releases/download/v1.0/honcho-pi-installer.sh | bash`
- Idempotent: Safe to re-run; detects existing installs.
- Interactive: Prompts for key configs (DB, LLM keys) with smart defaults/checks.
- Portable: Assumes Linux/Debian-like (common for Pi setups); handles dep installs (UV, Docker).
- Versioned: Tied to Honcho v3.0.3 fork with dgs-integrations (obs hooks, Git branch support).

**Target Users**: Pi-mono developers wanting local agentic memory without manual setup. Install time: <5min on clean Ubuntu.

**Non-Goals**: Full uninstaller (add as flag); Windows/Mac support (focus Linux); Managed Honcho (local-only).

## Package Structure

The installer will download/extract a tarball containing:

- **Honcho Core** (`honcho/` dir):
  - `src/`: Python source (main.py, deriver.py, crud/, reranker_client.py).
  - `scripts/`: install-systemd.sh, run-tests.sh.
  - `pyproject.toml`, `uv.lock`: For UV dep management.
  - `.env.template`: Pre-filled with defaults (e.g., local DB URI).
  - `config.toml.example`: Base config (reranker: bge-reranker-large:f16).
  - `migrations/`, `alembic.ini`: For DB setup.
  - `docs/`: Subset (README, this guide).

- **Pi Integration** (`pi-extension/` dir):
  - `honcho.ts`: Extension with obs hooks, local fallbacks.
  - `settings-honcho.json`: Snippet to merge into Pi's `~/.pi/agent/settings.json` (e.g., enable honcho tools).
  - `commands/`: /honcho-obs-* slash commands.

- **Services & Utils** (`services/` dir):
  - `honcho-api.service`, `honcho-deriver.service`: Systemd units (ExecStart via uv run).
  - `docker-compose-db.yml`: For pgvector Postgres (optional).
  - `verify-install.sh`: Post-install checks (API ping, Pi restart).

- **Metadata**:
  - `VERSION`: e.g., "1.0.0-dgs".
  - `SHA256SUM`: For tar integrity.
  - `LICENSE`: AGPL-3.0 excerpt.

**Tar Size Estimate**: ~50-150MB (excludes .venv—regenerated). Exclude: logs, .git, temp files.

**Distribution**: GitHub release (raw tar URL); script embeds base64 tar or downloads it.

## Installer Script Design (`honcho-pi-installer.sh`)

~400-600 lines, bash 4+. Structure:

1. **Header & Checks** (Lines 1-50):
   - Shebang: `#!/bin/bash`.
   - Warnings: "This installs Honcho for Pi—review code at [GitHub]. Run as non-root."
   - Idempotence: `if [ -d "$HOME/.local/lib/honcho-pi" ] && [ "$1" != "--force" ]; then echo "Already installed. Use --uninstall or --force."; exit 0; fi`
   - Deps: Check `curl`, `tar`, `jq` (for JSON merge); install via apt if missing (prompt: "Install missing deps? y/N").
   - UV: If ! command -v uv, curl install from astral.sh + add to PATH.

2. **Download & Extract** (Lines 50-100):
   - Download tar: `curl -LO https://github.com/.../honcho-pi.tar.gz`
   - Verify: `echo "$EXPECTED_SHA256  honcho-pi.tar.gz" | sha256sum -c` (fail if mismatch).
   - Extract: `tar -xzf honcho-pi.tar.gz -C "$HOME/.local/lib/"`
   - Chdir: `cd "$HOME/.local/lib/honcho-pi"`

3. **Interactive Configuration** (Lines 100-300): Prompt-driven with defaults/checks. Use `read -p` with validation.

   - **DB Setup**:
     - Prompt: "Use Docker Postgres with pgvector? (y/N, default: y)"
     - If y: `docker compose -f docker-compose-db.yml up -d` (pulls ankane/pgvector image; sets POSTGRES_PASSWORD=honcho_default).
     - Else: Prompt "Enter Postgres URI (default: postgresql+psycopg://user:pass@localhost:5432/honcho)? "
     - Check: `psql "$DB_URI" -c "CREATE EXTENSION IF NOT EXISTS vector;"` (fail if no pgvector: "Install pgvector extension!").
     - Migrate: `uv sync && uv run alembic upgrade head` (create tables).

   - **LLM/Embedding/Reranker Configs** (Check existing, prompt overrides):
     - Existing Check: `if [ -n "$OPENAI_API_KEY" ]; then echo "Using existing OpenAI key."; else read -p "Enter OpenAI API key for embeddings (default: none, skip for local)? "; fi`
     - Prompts:
       - "LLM Provider for Dialectic (Anthropic/OpenAI/Groq/Gemini, default: Anthropic)? " → Set `LLM_ANTHROPIC_API_KEY` etc.
       - "Embedding Model (OpenAI ada-002/local Ollama, default: OpenAI)? " → If local: Check Ollama running (`ollama list`), prompt model pull (`ollama pull nomic-embed-text`).
       - "Reranker Enabled? (y/N, default: y; uses bge-reranker-large:f16 via Ollama)? " → If y: Check Ollama (`ollama pull qllama/bge-reranker-large:f16`), set `RERANKER_ENABLED=true`.
       - "Other Envs? (e.g., Sentry for monitoring: y/N)? "
     - Generate .env: `cp .env.template .env && source .env` (export vars).

   - **Pi-Specific**:
     - Check Pi install: `if [ ! -d "$HOME/.pi" ]; then echo "Pi not found—install first?"; exit 1; fi`
     - Prompt: "Enable obs hooks in Pi? (y/N, default: y)" → Merge settings snippet via `jq '. + load("settings-honcho.json")' ~/.pi/agent/settings.json > tmp && mv tmp ~/.pi/agent/settings.json`.
     - Copy extension: `cp pi-extension/honcho.ts ~/.pi/agent/extensions/`.

   - **Additional Env Questions** (Smart Defaults):
     - "Port for Honcho API (default: 8000)? " → Validate unused (`netstat -tuln | grep :8000`).
     - "Git Branch Integration? (y/N, default: y; detects repo branches for obs)? "
     - "Local Cache Size for Fallbacks (MB, default: 100)? " → Set in config.toml.
     - "Dreaming Enabled? (background synthesis, default: y; requires deriver service)? "
     - "Telemetry/Metrics? (Prometheus/Sentry, default: n for privacy)? "
     - Validation: For each key/provider, test (e.g., `curl -H "Authorization: Bearer $API_KEY" https://api.openai.com/v1/models` → "Valid!" or error).

4. **Installation Steps** (Lines 300-400):
   - UV Sync: `uv sync --frozen` (uses uv.lock for reproducibility).
   - Services: `./scripts/install-systemd.sh` → `systemctl daemon-reload && systemctl enable --now honcho-api honcho-deriver`.
   - Pi Restart: `pkill -f "pi-mono" || true; sleep 5; # Pi auto-restarts via supervisor?`
   - Permissions: `chmod +x scripts/*; chown -R $USER:$USER .`

5. **Verification & Cleanup** (Lines 400-450):
   - Tests: `./verify-install.sh` (curl API health, psql table check, Pi extension load via `pi status` if available).
   - Output: "Success! API at http://localhost:8000. Test: /honcho-obs-status in Pi. Logs: journalctl -u honcho-api."
   - Cleanup: `rm honcho-pi.tar.gz`.
   - Flags: `--uninstall`: Stop services, rm -rf ~/.local/lib/honcho-pi, revert settings.json.

**Error Handling**: Trap SIGINT, log to ~/honcho-install.log, rollback on fail (e.g., rm partial dir).

## Creation Steps

To build the installer (run once on your machine):

1. **Prepare Package Dir** (Local Build):
   ```bash
   mkdir -p ~/honcho-pi-package/{honcho,pi-extension,services}
   cp -r ~/.local/lib/honcho/{src,scripts,pyproject.toml,uv.lock,.env.template,config.toml.example,alembic.ini,migrations,docker-compose-db.yml} ~/honcho-pi-package/honcho/
   cp ~/.pi/agent/extensions/honcho.ts ~/honcho-pi-package/pi-extension/
   # Add custom: obs hooks, Git integration files
   cp -r your-systemd-units/* ~/honcho-pi-package/services/
   # Generate settings snippet
   echo '{"honcho": {"enabled": true, "api_url": "http://localhost:8000"}}' > ~/honcho-pi-package/pi-extension/settings-honcho.json
   # Exclude sensitive: grep -v API_KEY honcho/.env > honcho/.env.template
   ```

2. **Create Tar**:
   ```bash
   cd ~/honcho-pi-package
   tar -czf ../honcho-pi.tar.gz .
   cd .. && sha256sum honcho-pi.tar.gz > SHA256SUM  # For verification
   ```

3. **Write Installer Script**:
   - Draft in editor (use above structure).
   - Embed SHA: `EXPECTED_SHA256=$(cat SHA256SUM | cut -d' ' -f1)`
   - Download Logic: `curl -LO $TAR_URL` (GitHub raw/release URL).
   - Test: `bash honcho-pi-installer.sh --dry-run` (add flag to simulate).

4. **Test on Clean Env**:
   - VM (Ubuntu 22.04): `curl | bash` → Verify services up, Pi extension loads, API responds.
   - Edge Cases: No Docker (prompt manual DB), existing keys (skip prompt), force re-install.

5. **Version & Release**:
   - Tag: v1.0.0 (include changelog: "Initial Pi installer with obs hooks").
   - GitHub: Create repo (honcho-pi-installer), upload tar/script as release assets.
   - Docs: Update Pi README with install command; add to Honcho docs here.

6. **Maintenance**:
   - Updates: Bump version, rebuild tar on changes (e.g., new hooks).
   - Security: Sign script (gpg), audit prompts for secrets.

## Potential Enhancements
- **Advanced Prompts**: "Use existing Ollama? (y/N)" → Integrate local models without keys.
- **Profiles**: "Minimal (no rerank)" vs. "Full (with Dreaming)".
- **Post-Install**: Auto-run `honcho start` alias in .bashrc.
- **Unattended**: `--non-interactive` with env vars from file.

This design ensures a smooth, guided install while validating configs early. Next: Prototype the script? 

*Generated: 2026-04-05 | Version: Draft 1.0*
