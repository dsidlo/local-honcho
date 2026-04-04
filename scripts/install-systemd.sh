#!/bin/bash
# Honcho systemd Service Installation Script
# Installs user services for API server and deriver

set -e

echo "=== Honcho systemd Service Installer ==="

# Configuration
HONCHO_DIR="${HONCHO_DIR:-$HOME/.local/lib/honcho}"
SERVICE_DIR="$HOME/.config/systemd/user"
USER_ENV="$HOME/.env"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check if systemd is available
    if ! command -v systemctl &> /dev/null; then
        log_error "systemctl not found. Is systemd installed?"
        exit 1
    fi
    
    # Check ifHoncho directory exists
    if [ ! -d "$HONCHO_DIR" ]; then
        log_warn "Honcho directory not found at $HONCHO_DIR"
        echo ""
        echo "Would you like to:"
        echo "  1) Copy from current workspace ($(pwd))"
        echo "  2) Clone from GitHub"
        echo "  3) Specify custom path"
        echo "  4) Exit"
        read -p "Choice [1]: " choice
        choice=${choice:-1}
        
        case $choice in
            1)
                log_info "Copying from $(pwd) to $HONCHO_DIR..."
                mkdir -p "$(dirname "$HONCHO_DIR")"
                cp -r "$(pwd)" "$HONCHO_DIR"
                ;;
            2)
                log_info "Cloning from GitHub..."
                mkdir -p "$(dirname "$HONCHO_DIR")"
                git clone https://github.com/plastic-labs/honcho.git "$HONCHO_DIR"
                ;;
            3)
                read -p "Enter custom path: " custom_path
                HONCHO_DIR="$custom_path"
                ;;
            4)
                exit 1
                ;;
        esac
    fi
    
    # Check for virtual environment
    if [ ! -d "$HONCHO_DIR/.venv" ]; then
        log_warn "Virtual environment not found at $HONCHO_DIR/.venv"
        log_info "Creating virtual environment with uv..."
        cd "$HONCHO_DIR"
        uv sync
    fi
    
    # Check for .env file
    if [ ! -f "$USER_ENV" ]; then
        log_warn "Environment file not found at $USER_ENV"
        log_info "Creating from template..."
        
        cat > "$USER_ENV" << 'ENVFILE'
# Honcho Configuration
HONCHO_BASE_URL=http://localhost:8000
HONCHO_WORKSPACE=default
HONCHO_USER=dsidlo
HONCHO_AGENT_ID=agent-pi-mono
HONCHO_WORKSPACE_MODE=auto

# Database
DB_CONNECTION_URI=postgresql+psycopg://dsidlo@localhost:5433/postgres

# LLM Configuration
LLM_VLLM_BASE_URL=http://localhost:11434/v1
LLM_VLLM_API_KEY=ollama
LLM_EMBEDDING_PROVIDER=ollama
LLM_OLLAMA_BASE_URL=http://localhost:11434
LLM_OLLAMA_EMBEDDING_MODEL=nomic-embed-text:latest

# Deriver
DERIVER_PROVIDER=vllm
DERIVER_MODEL=qwen3.5:397b-cloud
DERIVER_MAX_OUTPUT_TOKENS=4096
DERIVER_THINKING_BUDGET_TOKENS=1024
DERIVER_DEDUPLICATE=true

# Add your other DERIVER, DIALECTIC, DREAM settings here
ENVFILE
        
        log_warn "Please edit $USER_ENV with your actual configuration"
        read -p "Press Enter to continue or Ctrl+C to exit..."
    fi
    
    log_info "Prerequisites check complete"
}

# Create service files
create_services() {
    log_info "Creating systemd service files..."
    
    mkdir -p "$SERVICE_DIR"
    
    # Create API service
    cat > "$SERVICE_DIR/honcho-api.service" << EOF
[Unit]
Description=Honcho API Server
Documentation=https://docs.honcho.dev
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
WorkingDirectory=$HONCHO_DIR
ExecStart=$HONCHO_DIR/.venv/bin/uv run --no-dev fastapi dev src/main.py --host 0.0.0.0 --port 8000
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=$USER_ENV
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3
TimeoutStopSec=30
KillSignal=SIGTERM

[Install]
WantedBy=default.target
EOF

    # Create Deriver service
    cat > "$SERVICE_DIR/honcho-deriver.service" << EOF
[Unit]
Description=Honcho Deriver (Background Worker)
Documentation=https://docs.honcho.dev
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HONCHO_DIR
ExecStart=$HONCHO_DIR/.venv/bin/uv run --no-dev python -m src.deriver
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=$USER_ENV
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3
TimeoutStopSec=30
KillSignal=SIGTERM

[Install]
WantedBy=default.target
EOF

    chmod 644 "$SERVICE_DIR/honcho-api.service"
    chmod 644 "$SERVICE_DIR/honcho-deriver.service"
    
    log_info "Service files created at:"
    log_info "  - $SERVICE_DIR/honcho-api.service"
    log_info "  - $SERVICE_DIR/honcho-deriver.service"
}

# Reload and enable systemd
configure_systemd() {
    log_info "Configuring systemd..."
    
    # Reload systemd
    systemctl --user daemon-reload
    
    # Enable services
    systemctl --user enable honcho-api.service
    systemctl --user enable honcho-deriver.service
    
    log_info "Services enabled for auto-start"
}

# Show status
show_status() {
    echo ""
    echo "=== Service Status ==="
    echo ""
    systemctl --user status honcho-api.service --no-pager || true
    echo ""
    systemctl --user status honcho-deriver.service --no-pager || true
}

# Main
echo ""
echo "This script will:"
echo "  1. Check prerequisites (Honcho installation, .env file)"
echo "  2. Create systemd user service files"
echo "  3. Enable services for auto-start"
echo ""
read -p "Continue? [Y/n]: " confirm
confirm=${confirm:-Y}

if [[ $confirm =~ ^[Yy]$ ]]; then
    check_prerequisites
    create_services
    configure_systemd
    
    echo ""
    log_info "Installation complete!"
    echo ""
    echo "Next steps:"
    echo "  Start services:     systemctl --user start honcho-api honcho-deriver"
    echo "  Check status:         systemctl --user status honcho-api honcho-deriver"
    echo "  View logs:            journalctl --user -u honcho-api -f"
    echo "  Stop services:        systemctl --user stop honcho-api honcho-deriver"
    echo ""
    read -p "Start services now? [Y/n]: " start_now
    start_now=${start_now:-Y}
    
    if [[ $start_now =~ ^[Yy]$ ]]; then
        log_info "Starting services..."
        systemctl --user start honcho-api.service
        sleep 2
        systemctl --user start honcho-deriver.service
        sleep 2
        show_status
    fi
else
    log_info "Installation cancelled"
    exit 0
fi
