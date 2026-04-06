#!/usr/bin/env python3
\"\"\"Build script for Honcho Pi Installer distribution.
Creates package from ~/.local/lib/honcho, builds compressed tar in dist/.
Then embeds the full tar as base64 into LocalHoncho_installer.sh for self-containment.
Run: python scripts/dist_script.py
\"\"\"

import os
import shutil
import tarfile
import hashlib
import subprocess
from pathlib import Path

# Constants
SOURCE_DIR = Path.home() / '.local' / 'lib' / 'honcho'
PACKAGE_DIR = Path.cwd() / 'honcho-pi-package'
DIST_DIR = Path.cwd() / 'dist'
TARBALL_NAME = 'honcho-pi.tar.gz'
INSTALLER_NAME = 'LocalHoncho_installer.sh'
EXCLUDED_DIRS = ['logs', '__pycache__', '.git', '.idea', '.venv']
EXCLUDED_FILES = ['.env', '*.pyc', 'uv.lock']  # Patterns for ignore
INCLUDED_FILES = ['.env.template', 'config.toml.example', 'pyproject.toml', 'alembic.ini', 'docker-compose.yml.example', 'Dockerfile', 'README.md', 'LICENSE']

def main():
    print("Building Honcho Pi Installer Package...")
    
    # Clean previous builds
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    PACKAGE_DIR.mkdir()
    DIST_DIR.mkdir()
    
    # Copy Honcho core
    honcho_target = PACKAGE_DIR / 'honcho'
    honcho_target.mkdir()
    
    def ignore_func(src, names):
        ignored = set()
        for name in names:
            if any(name.startswith(ex) for ex in EXCLUDED_DIRS) or any(ex in name for ex in EXCLUDED_FILES):
                ignored.add(name)
        return ignored
    
    for item in SOURCE_DIR.iterdir():
        if item.is_dir() and item.name not in EXCLUDED_DIRS:
            shutil.copytree(item, honcho_target / item.name, ignore=ignore_func)
        elif item.is_file() and item.name in INCLUDED_FILES:
            shutil.copy2(item, honcho_target / item.name)
    
    # Add Pi extension placeholder
    pi_ext_dir = PACKAGE_DIR / 'pi-extension'
    pi_ext_dir.mkdir()
    with open(pi_ext_dir / 'honcho.ts', 'w') as f:
        f.write('// Placeholder for honcho.ts extension\n// TODO: Copy from ~/.pi/agent/extensions/honcho.ts if exists\n')
    with open(pi_ext_dir / 'settings-honcho.json', 'w') as f:
        f.write('{\"honcho\": {\"enabled\": true, \"api_url\": \"http://localhost:8000\"}}\n')
    
    # Add services dir
    services_dir = PACKAGE_DIR / 'services'
    services_dir.mkdir()
    with open(services_dir / 'honcho-api.service', 'w') as f:
        f.write('[Unit]\nDescription=Honcho API Server\nAfter=network.target\n\n[Service]\nType=simple\nUser=%i\nWorkingDirectory=%h/.local/lib/honcho-pi/honcho\nEnvironment=PATH=%h/.local/bin\nExecStart=%h/.local/bin/uv run -m src.main --host 0.0.0.0 --port 8000\nRestart=always\n\n[Install]\nWantedBy=default.target\n')
    with open(services_dir / 'honcho-deriver.service', 'w') as f:
        f.write('[Unit]\nDescription=Honcho Deriver (Background Worker)\nAfter=network.target honcho-api.service\n\n[Service]\nType=simple\nUser=%i\nWorkingDirectory=%h/.local/lib/honcho-pi/honcho\nEnvironment=PATH=%h/.local/bin\nExecStart=%h/.local/bin/uv run -m src.deriver\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\n')
    with open(services_dir / 'verify-install.sh', 'w') as f:
        f.write('#!/bin/bash\n# Post-install verification\nset -e\n\n# Check API\ncurl -f http://localhost:8000/health || { echo \"API not responding\"; exit 1; }\n\n# Check DB\npsql \"$DB_URI\" -c \"SELECT 1;\" || { echo \"DB not connected\"; exit 1; }\n\n# Check Pi extension (if Pi installed)\nif [ -d ~/.pi ]; then\n  pi status | grep honcho || echo \"Warning: Honcho not in Pi status\"\nfi\n\necho \"Installation verified!\"\n')
    os.chmod(services_dir / 'verify-install.sh', 0o755)
    
    # Add metadata
    with open(PACKAGE_DIR / 'VERSION', 'w') as f:
        f.write('1.0.0-dgs\n')
    with open(PACKAGE_DIR / 'README-install.txt', 'w') as f:
        f.write('Honcho Pi Installer v1.0.0\n\nRun the installer script to deploy.\nSee docs/v3/guides/community/dgs-integrations/Local-Honcho-Installer-Design.md for details.\n')
    
    # Copy LICENSE excerpt
    license_path = SOURCE_DIR / 'LICENSE'
    if license_path.exists():
        with open(PACKAGE_DIR / 'LICENSE', 'w') as f:
            f.write(license_path.read_text()[:1000] + '\n... (full license in source)\n')
    
    # Create compressed tarball
    tar_path = DIST_DIR / TARBALL_NAME
    with tarfile.open(tar_path, 'w:gz') as tar:
        tar.add(PACKAGE_DIR, arcname='.')
    
    print(f"Compressed tar created: {tar_path} ({tar_path.stat().st_size / (1024*1024):.1f} MB)")
    
    # Generate SHA256
    sha256_hash = hashlib.sha256()
    with open(tar_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256_hash.update(chunk)
    sha = sha256_hash.hexdigest()
    with open(DIST_DIR / 'SHA256SUM', 'w') as f:
        f.write(f'{sha}  {TARBALL_NAME}\n')
    
    print(f"SHA: {sha}")
    
    # Embed into LocalHoncho_installer.sh (self-contained)
    print("Assembling self-contained LocalHoncho_installer.sh with embedded tar...")
    
    # Generate base64 of compressed tar (full, no wrap)
    base64_tar = subprocess.check_output(['base64', '-w', '0', str(tar_path)]).decode('utf-8').strip()
    print(f"Base64 generated ({len(base64_tar)} chars)")
    
    # Full bash installer template with embedded data
    installer_content = f'''#!/bin/bash
#
# LocalHoncho_installer.sh - Self-Contained Honcho Pi Installer
# Based on Local-Honcho-Installer-Design.md
# Usage: bash LocalHoncho_installer.sh [--force] [--non-interactive] [--uninstall]

set -euo pipefail  # Strict mode

# Colors
RED='\\033[0;31m'
GREEN='\\033[0;32m'
YELLOW='\\033[1;33m'
NC='\\033[0m'

# Globals
SCRIPT_NAME="$(basename "$0")"
INSTALL_DIR="$HOME/.local/lib/honcho-pi"
EXPECTED_SHA="{sha}"
VERSION="1.0.0-dgs"

# EMBEDDED_TAR_B64 (full base64 of compressed honcho-pi.tar.gz - embedded tar data)
EMBEDDED_TAR_B64="{base64_tar}"

print_status() {{
    echo -e "${{GREEN}}[INFO]${{NC}} $1"
}}

print_warning() {{
    echo -e "${{YELLOW}}[WARN]${{NC}} $1"
}}

print_error() {{
    echo -e "${{RED}}[ERROR]${{NC}} $1" >&2
    exit 1
}}

check_already_installed() {{
    if [ -d "$INSTALL_DIR" ] && [ "$FORCE" != "true" ]; then
        print_warning "Honcho Pi already installed at $INSTALL_DIR."
        print_status "Use --force to reinstall or --uninstall to remove."
        exit 0
    fi
}}

install_deps() {{
    local missing=()
    for dep in curl tar jq; do
        if ! command -v "$dep" &> /dev/null; then
            missing+=("$dep")
        fi
    done
    
    if [ ${{ #missing[@] }} -ne 0 ]; then
        print_warning "Missing dependencies: ${{missing[*]}}"
        if [ "$NON_INTERACTIVE" = "true" ]; then
            print_error "Install deps manually (apt install ${{missing[*]}})."
        fi
        read -p "Install via apt? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo apt update
            sudo apt install -y "${{missing[@]}}"
        else
            print_error "Please install deps and re-run."
        fi
    fi
    
    # Install UV if missing
    if ! command -v uv &> /dev/null; then
        print_status "Installing UV..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
}}

extract_tar() {{
    local tmp_tar=$(mktemp honcho-tar.XXXXXX.tar.gz)
    
    print_status "Extracting embedded compressed tar data..."
    echo "$EMBEDDED_TAR_B64" | base64 -d > "$tmp_tar"
    
    # Verify SHA of decoded compressed tar
    computed_sha=$(sha256sum "$tmp_tar" | cut -d' ' -f1)
    if [ "$computed_sha" != "$EXPECTED_SHA" ]; then
        print_error "Embedded tar integrity failed! SHA mismatch (tampered?)."
    fi
    
    # Extract compressed tar
    tar -xzf "$tmp_tar" -C "$HOME/.local/lib/"
    rm "$tmp_tar"
    
    if [ ! -d "$INSTALL_DIR" ]; then
        print_error "Extraction failed - $INSTALL_DIR not created."
    fi
    
    print_status "Embedded tar extracted to $INSTALL_DIR"
}}

prompt_db_setup() {{
    if [ "$NON_INTERACTIVE" = "true" ]; then
        USE_DOCKER=y
    else
        read -p "Use Docker Postgres with pgvector? (y/N) [y]: " -n 1 -r USE_DOCKER
        echo
        USE_DOCKER=${{USE_DOCKER:-y}}
    fi
    
    if [[ $USE_DOCKER =~ ^[Yy]$ ]]; then
        local docker_file="$INSTALL_DIR/honcho/docker-compose.yml.example"
        if [ -f "$docker_file" ]; then
            print_status "Starting Docker Postgres with pgvector..."
            cd "$INSTALL_DIR/honcho"
            docker compose -f docker-compose.yml.example up -d
            cd -
            DB_URI="postgresql+psycopg://postgres:password@localhost:5432/honcho"
        else
            print_error "docker-compose.yml.example not found in embedded tar."
        fi
    else
        read -p "Enter Postgres URI (default: postgresql+psycopg://user:pass@localhost:5432/honcho): " DB_URI
        DB_URI=${{DB_URI:-"postgresql+psycopg://user:pass@localhost:5432/honcho"}}
    fi
    
    # Test connection and pgvector
    export PGPASSWORD=$(echo "$DB_URI" | sed 's/.*:\/\///' | cut -d'@' -f1 | cut -d':' -f2)
    psql "$DB_URI" -c "CREATE EXTENSION IF NOT EXISTS vector;" &> /dev/null || print_error "DB connection failed or pgvector extension missing. Check URI."
    
    print_status "DB setup complete: $DB_URI"
}}

generate_env() {{
    local env_file="$INSTALL_DIR/honcho/.env"
    local template="$INSTALL_DIR/honcho/.env.template"
    
    if [ ! -f "$template" ]; then
        print_error ".env.template not found in embedded tar."
    fi
    
    cp "$template" "$env_file"
    echo "DATABASE_URL=$DB_URI" >> "$env_file"
    
    local port
    read -p "API Port [8000]: " port
    port=${{port:-8000}}
    echo "API_PORT=$port" >> "$env_file"
    
    local dreaming
    read -p "Enable Dreaming? (y/N) [y]: " -n 1 -r dreaming
    echo
    if [[ $dreaming =~ ^[Yy]$ ]]; then
        echo "DREAMING_ENABLED=true" >> "$env_file"
    fi
    
    chmod 600 "$env_file"
    print_status ".env generated from template - review/edit $env_file for LLM keys."
}}

prompt_llm_config() {{
    local provider
    read -p "LLM Provider (anthropic/openai/groq/gemini) [anthropic]: " provider
    provider=${{provider:-"anthropic"}}
    
    case $provider in
        anthropic)
            local key
            read -s -p "Anthropic API key: " key
            echo
            if [ -n "$key" ]; then
                echo "LLM_ANTHROPIC_API_KEY=$key" >> "$env_file"
                # Test (silent)
                curl -s -H "x-api-key: $key" https://api.anthropic.com/v1/messages > /dev/null || print_warning "Key test failed - check provider."
            fi
            ;;
        openai)
            local key
            read -s -p "OpenAI API key: " key
            echo
            if [ -n "$key" ]; then
                echo "LLM_OPENAI_API_KEY=$key" >> "$env_file"
                curl -s -H "Authorization: Bearer $key" https://api.openai.com/v1/models > /dev/null || print_warning "Key test failed."
            fi
            ;;
        # Add groq/gemini cases...
        *)
            print_warning "Provider $provider - add key manually to .env"
            ;;
    esac
    
    local embed_model
    read -p "Embedding model (openai/ollama) [openai]: " embed_model
    embed_model=${{embed_model:-"openai"}}
    case $embed_model in
        ollama)
            echo "LLM__OLLAMA_EMBEDDING_MODEL=nomic-embed-text" >> "$env_file"
            ollama pull nomic-embed-text || print_warning "Pull Ollama embedding model manually."
            ;;
        openai)
            # Reuse OpenAI key or prompt
            ;;
    esac
    
    local rerank
    read -p "Enable Reranker? (y/N) [y]: " -n 1 -r rerank
    echo
    if [[ $rerank =~ ^[Yy]$ ]]; then
        echo "RERANKER_ENABLED=true" >> "$env_file"
        echo "RERANKER_MODEL=qllama/bge-reranker-large:f16" >> "$env_file"
        ollama pull qllama/bge-reranker-large:f16 || print_warning "Reranker model pull failed - install manually."
    fi
    
    print_status "LLM config added to .env."
}}

setup_db_migration() {{
    cd "$INSTALL_DIR/honcho"
    print_status "Syncing dependencies with UV..."
    uv sync --frozen
    
    print_status "Running DB migrations..."
    uv run alembic upgrade head
    
    cd -
    print_status "Setup complete."
}}

integrate_pi() {{
    local pi_dir="$HOME/.pi/agent"
    if [ ! -d "$pi_dir" ]; then
        print_warning "Pi not found at $pi_dir. Skip integration - install Pi first."
        return
    fi
    
    local settings="$pi_dir/settings.json"
    local snippet="$INSTALL_DIR/pi-extension/settings-honcho.json"
    
    if [ -f "$settings" ] && [ -f "$snippet" ]; then
        cp "$settings" "$settings.bak.$(date +%s)"
        jq -s '.[0] * .[1]' "$settings" "$snippet" > "$settings.tmp" && mv "$settings.tmp" "$settings"
        print_status "Pi settings merged (honcho enabled)."
        
        local ext_dir="$pi_dir/extensions"
        mkdir -p "$ext_dir"
        cp "$INSTALL_DIR/pi-extension/honcho.ts" "$ext_dir/"
        print_status "Pi extension copied to $ext_dir."
    else
        print_warning "Pi settings.json or snippet missing - manual merge needed."
    fi
}}

install_services() {{
    print_status "Installing systemd services..."
    
    # Reload daemon
    systemctl --user daemon-reload
    
    local services=("honcho-api" "honcho-deriver")
    for svc in "${{services[@]}}"; do
        local svc_file="$INSTALL_DIR/services/$svc.service"
        if [ -f "$svc_file" ]; then
            # Copy to user systemd dir
            cp "$svc_file" "$HOME/.config/systemd/user/"
            systemctl --user daemon-reload
            systemctl --user enable "$svc"
            systemctl --user start "$svc"
            print_status "Service $svc enabled and started."
        else
            print_error "Service file $svc.service missing in embedded tar."
        fi
    done
    
    print_status "Services installed. Check: systemctl --user status honcho-*"
}}

verify_install() {{
    local verify_script="$INSTALL_DIR/services/verify-install.sh"
    if [ -f "$verify_script" ]; then
        print_status "Running verification..."
        DB_URI="$DB_URI" bash "$verify_script"
    else
        print_status "Manual verification:"
        echo "- API: curl http://localhost:8000/health"
        echo "- DB: psql '$DB_URI' -c 'SELECT 1;'"
        echo "- Services: systemctl --user status honcho-api"
        if [ -d "$HOME/.pi/agent" ]; then
            echo "- Pi: Check ~/.pi/agent/settings.json for 'honcho' entry"
        fi
    fi
}}

uninstall() {{
    if [ ! -d "$INSTALL_DIR" ]; then
        print_status "No installation found."
        exit 0
    fi
    
    read -p "Uninstall Honcho Pi? Removes $INSTALL_DIR and services (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
    
    # Stop services
    systemctl --user stop honcho-api honcho-deriver 2>/dev/null || true
    systemctl --user disable honcho-api honcho-deriver 2>/dev/null || true
    systemctl --user daemon-reload
    
    # Remove dir
    rm -rf "$INSTALL_DIR"
    
    # Remove service files
    rm -f "$HOME/.config/systemd/user/honcho-"*.service"
    
    # Docker down if used
    local docker_file="$INSTALL_DIR/honcho/docker-compose.yml.example"
    if [ -f "$docker_file" ]; then
        cd "$INSTALL_DIR/honcho" 2>/dev/null || true
        docker compose -f docker-compose.yml.example down 2>/dev/null || true
        cd - > /dev/null 2>/dev/null || true
    fi
    
    print_warning "Pi settings not auto-reverted - edit ~/.pi/agent/settings.json manually to remove 'honcho'."
    
    print_status "Uninstallation complete."
}}

# Parse args
FORCE=false
NON_INTERACTIVE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --force) FORCE=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --uninstall) uninstall; exit 0 ;;
        *) print_error "Unknown option $1" ;;
    esac
done

print_status "LocalHoncho Installer v$VERSION"
print_status "Target: $INSTALL_DIR"

check_already_installed
install_deps
extract_tar
prompt_db_setup
generate_env
prompt_llm_config
setup_db_migration
integrate_pi
install_services
verify_install

print_status "Installation complete!"
print_status "API: http://localhost:8000"
print_status "Logs: journalctl --user -u honcho-api -f"
print_status "Test in Pi: Restart Pi and run /honcho-obs-status if extension loaded."

# Add PATH if needed
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    print_status "Added UV to .bashrc - source ~/.bashrc or restart shell."
fi
'''
    
    installer_path = DIST_DIR / INSTALLER_NAME
    with open(installer_path, 'w') as f:
        f.write(installer_content)
    os.chmod(installer_path, 0o755)
    
    print(f"Self-contained {INSTALLER_NAME} assembled: {installer_path} (~{installer_path.stat().st_size / (1024*1024):.1f} MB with full embedded compressed tar data)")
    print(f"Embedded tar SHA: {sha}")
    print("Test: bash dist/LocalHoncho_installer.sh (offline extraction works)")

if __name__ == '__main__':
    main()