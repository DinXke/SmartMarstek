#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

ok()  { echo -e "${GREEN}[OK]${RESET}  $*"; }
err() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
hdr() { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

hdr "=============================================="
hdr "  Marstek Dashboard Installer (Linux/macOS)"
hdr "=============================================="
echo ""

# ── Check Python 3 ───────────────────────────────────────────────────────────
PYTHON=""
for CMD in python3 python; do
    if command -v "$CMD" &>/dev/null; then
        VER=$("$CMD" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)
        if [ "$VER" -ge 3 ]; then
            PYTHON="$CMD"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "Python 3 is not installed."
    echo ""
    echo "  Install it with one of:"
    echo "    Ubuntu/Debian:  sudo apt install python3 python3-venv python3-pip"
    echo "    Fedora/RHEL:    sudo dnf install python3"
    echo "    macOS:          brew install python3"
    echo "    Arch:           sudo pacman -S python"
    echo ""
    exit 1
fi
ok "Python: $($PYTHON --version)"

# Check python3-venv is available (Debian/Ubuntu sometimes needs it separately)
if ! $PYTHON -m venv --help &>/dev/null 2>&1; then
    err "Python venv module not found."
    echo "  Install it with: sudo apt install python3-venv"
    exit 1
fi

# ── Check Node.js ─────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
    err "Node.js is not installed."
    echo ""
    echo "  Install it with one of:"
    echo "    Ubuntu/Debian:  sudo apt install nodejs npm"
    echo "    Fedora/RHEL:    sudo dnf install nodejs"
    echo "    macOS:          brew install node"
    echo "    Any:            https://nodejs.org/"
    echo ""
    exit 1
fi
ok "Node.js: $(node --version)"

if ! command -v npm &>/dev/null; then
    err "npm not found. Please reinstall Node.js."
    exit 1
fi
ok "npm: $(npm --version)"
echo ""

# ── Step 1: Python virtual environment ───────────────────────────────────────
echo "[1/4] Creating Python virtual environment..."
cd "$BACKEND_DIR"
if [ -d venv ]; then
    echo "      Already exists, skipping creation."
else
    $PYTHON -m venv venv
fi

# ── Step 2: Python dependencies ──────────────────────────────────────────────
echo "[2/4] Installing Python dependencies..."
# shellcheck disable=SC1091
source "$BACKEND_DIR/venv/bin/activate"
pip install -r "$BACKEND_DIR/requirements.txt" -q --disable-pip-version-check
echo "      Done."

# ── Step 3: npm install ───────────────────────────────────────────────────────
echo "[3/4] Installing frontend (npm) dependencies..."
cd "$FRONTEND_DIR"
npm install --silent --no-fund --no-audit
echo "      Done."

# ── Step 4: Build React app ───────────────────────────────────────────────────
echo "[4/4] Building frontend..."
npm run build
echo "      Done."

# ── Make start.sh executable ─────────────────────────────────────────────────
chmod +x "$SCRIPT_DIR/start.sh"

# ── Optional: systemd service ────────────────────────────────────────────────
if command -v systemctl &>/dev/null && [ "$(id -u)" -ne 0 ]; then
    echo ""
    read -r -p "Install as a systemd user service (auto-start on login)? (y/n): " SVC
    if [[ "$SVC" =~ ^[Yy]$ ]]; then
        SERVICE_DIR="$HOME/.config/systemd/user"
        mkdir -p "$SERVICE_DIR"
        cat > "$SERVICE_DIR/marstek-dashboard.service" << EOF
[Unit]
Description=Marstek Battery Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$BACKEND_DIR
ExecStart=$BACKEND_DIR/venv/bin/python $BACKEND_DIR/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable marstek-dashboard.service
        systemctl --user start  marstek-dashboard.service
        ok "Systemd service installed and started."
        echo "      Manage with: systemctl --user {start|stop|status} marstek-dashboard"
    fi
fi

# ── Optional: desktop shortcut (Linux only) ───────────────────────────────────
if [ -d "$HOME/.local/share/applications" ] 2>/dev/null; then
    DESKTOP="$HOME/.local/share/applications/marstek-dashboard.desktop"
    cat > "$DESKTOP" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Marstek Dashboard
Comment=Monitor and control Marstek battery devices
Exec=bash -c 'cd "$SCRIPT_DIR" && ./start.sh'
Icon=battery
Terminal=true
Categories=Utility;
StartupNotify=true
EOF
    ok "Desktop shortcut created."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}=============================================="
echo "  Installation complete!"
echo ""
echo "  To start the dashboard, run:"
echo "     ./start.sh"
echo ""
echo "  The dashboard opens at http://localhost:5000"
echo -e "==============================================\n${RESET}"

read -r -p "Launch the dashboard now? (y/n): " LAUNCH
if [[ "$LAUNCH" =~ ^[Yy]$ ]]; then
    exec "$SCRIPT_DIR/start.sh"
else
    echo "Run ./start.sh whenever you want to open the dashboard."
fi
