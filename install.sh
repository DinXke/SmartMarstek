#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

ok()  { echo -e "${GREEN}[OK]${RESET}  $*"; }
err() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
hdr() { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }
warn() { echo -e "${YELLOW}[WARN]${RESET}  $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# SmartMarstek Standalone Docker Installer
# ─────────────────────────────────────────────────────────────────────────────

hdr "╭─────────────────────────────────────────────────────────────╮"
hdr "│   SmartMarstek Standalone Deployment Installer              │"
hdr "│   One command to deploy on a fresh Ubuntu machine           │"
hdr "╰─────────────────────────────────────────────────────────────╯"

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Check Ubuntu version
# ─────────────────────────────────────────────────────────────────────────────

if [ ! -f /etc/os-release ]; then
    err "This script requires a Debian/Ubuntu-based system."
    exit 1
fi

source /etc/os-release
if [[ ! "$ID" =~ ubuntu|debian ]]; then
    warn "This script is optimized for Ubuntu/Debian. Other systems may not be fully supported."
fi

ok "Detected: $PRETTY_NAME"

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Check/install Docker
# ─────────────────────────────────────────────────────────────────────────────

hdr "Checking Docker installation..."

if ! command -v docker &>/dev/null; then
    warn "Docker not found. Installing..."
    if command -v apt &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq docker.io docker-compose-v2 curl git
        ok "Docker installed"
    else
        err "Unable to install Docker. Please install manually:"
        echo "  https://docs.docker.com/engine/install/"
        exit 1
    fi
else
    DOCKER_VER=$(docker --version | grep -oP 'Docker version \K[0-9.]+' || echo "unknown")
    ok "Docker $DOCKER_VER already installed"
fi

# Check docker-compose
if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null; then
    err "docker-compose is not available. Please install Docker Compose v2+"
    exit 1
fi

ok "docker-compose available"

# Add current user to docker group if not already
if ! groups "$USER" | grep -q docker; then
    warn "Adding '$USER' to docker group (requires sudo)..."
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    echo "    Please logout and login again, or run: newgrp docker"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Clone or update SmartMarstek repo
# ─────────────────────────────────────────────────────────────────────────────

hdr "Setting up SmartMarstek repository..."

REPO_URL="https://github.com/SmartMarstek/GRIP.git"
REPO_DIR="${HOME}/smartmarstek"

if [ ! -d "$REPO_DIR" ]; then
    ok "Cloning SmartMarstek repository..."
    git clone "$REPO_URL" "$REPO_DIR"
else
    ok "SmartMarstek directory already exists: $REPO_DIR"
    read -r -p "Update to latest version? (y/n): " UPDATE_REPO
    if [[ "$UPDATE_REPO" =~ ^[Yy]$ ]]; then
        cd "$REPO_DIR"
        git fetch origin
        git pull origin main
        ok "Repository updated"
    fi
fi

cd "$REPO_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Create required directories
# ─────────────────────────────────────────────────────────────────────────────

hdr "Creating data directories..."

mkdir -p "$REPO_DIR/data"
mkdir -p "$REPO_DIR/config"
mkdir -p "$REPO_DIR/grafana/provisioning/dashboards"
mkdir -p "$REPO_DIR/grafana/provisioning/datasources"

ok "Data directories created"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Generate .env file
# ─────────────────────────────────────────────────────────────────────────────

hdr "Configuring environment..."

if [ -f "$REPO_DIR/.env" ]; then
    warn "Found existing .env file"
    read -r -p "Overwrite with fresh configuration? (y/n): " OVERWRITE_ENV
    if [[ ! "$OVERWRITE_ENV" =~ ^[Yy]$ ]]; then
        ok "Keeping existing .env"
    else
        cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    fi
else
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    ok "Created .env from template"
fi

# Prompt for optional configuration
echo ""
echo "Optional: Configure Home Assistant integration and ENTSO-E prices"
echo "(You can skip these now and configure them in the web UI later)"
echo ""

read -r -p "Home Assistant base URL (leave blank to skip): " HA_URL
if [ -n "$HA_URL" ]; then
    sed -i "s|^HA_URL=.*|HA_URL=$HA_URL|" "$REPO_DIR/.env"
fi

read -r -p "Home Assistant API token (leave blank to skip): " HA_TOKEN
if [ -n "$HA_TOKEN" ]; then
    # Escape special characters for sed
    HA_TOKEN_ESC=$(printf '%s\n' "$HA_TOKEN" | sed -e 's/[\/&]/\\&/g')
    sed -i "s|^HA_TOKEN=.*|HA_TOKEN=$HA_TOKEN_ESC|" "$REPO_DIR/.env"
fi

read -r -p "ENTSO-E API Key (leave blank to skip): " ENTSOE_KEY
if [ -n "$ENTSOE_KEY" ]; then
    sed -i "s|^ENTSOE_API_KEY=.*|ENTSOE_API_KEY=$ENTSOE_KEY|" "$REPO_DIR/.env"
fi

ok ".env configured"

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Start Docker containers
# ─────────────────────────────────────────────────────────────────────────────

hdr "Starting SmartMarstek services..."

if docker-compose up -d 2>/dev/null || docker compose up -d 2>/dev/null; then
    ok "Containers started"
else
    err "Failed to start containers"
    echo "Try running: cd $REPO_DIR && docker-compose up -d"
    exit 1
fi

# Wait for services to be healthy
hdr "Waiting for services to be ready (this may take 30-60 seconds)..."

for i in {1..60}; do
    if curl -sf http://localhost &>/dev/null; then
        ok "Services are healthy"
        break
    fi
    echo -n "."
    sleep 1
done

# ─────────────────────────────────────────────────────────────────────────────
# Step 7: Display access URLs
# ─────────────────────────────────────────────────────────────────────────────

hdr "╭─────────────────────────────────────────────────────────────╮"
hdr "│   SmartMarstek is ready!                                    │"
hdr "├─────────────────────────────────────────────────────────────┤"
hdr "│                                                              │"
echo -e "${BOLD}  🌐 SmartMarstek Dashboard:${RESET}"
echo -e "     ${CYAN}http://localhost${RESET}  (or http://$(hostname -I | awk '{print $1}'))  "
echo ""
echo -e "${BOLD}  📊 Grafana Dashboards:${RESET}"
echo -e "     ${CYAN}http://localhost/grafana${RESET}  (default: admin / admin)  "
echo ""
echo -e "${BOLD}  💾 Management:${RESET}"
echo -e "     View logs:    ${CYAN}cd $REPO_DIR && docker-compose logs -f${RESET}  "
echo -e "     Stop:         ${CYAN}cd $REPO_DIR && docker-compose down${RESET}  "
echo -e "     Restart:      ${CYAN}cd $REPO_DIR && docker-compose restart${RESET}  "
echo ""
echo -e "${BOLD}  📁 Data:${RESET}"
echo -e "     Configuration: ${CYAN}$REPO_DIR/.env${RESET}  "
echo -e "     Data dir:      ${CYAN}$REPO_DIR/data${RESET}  "
echo ""
hdr "│                                                              │"
hdr "╰─────────────────────────────────────────────────────────────╯"

echo ""
ok "Installation complete! Open http://localhost in your browser."
