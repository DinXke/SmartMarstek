#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

if [ ! -d "$BACKEND_DIR/venv" ]; then
    echo "[ERROR] Not installed yet. Please run ./install.sh first."
    exit 1
fi

if [ ! -d "$SCRIPT_DIR/frontend/dist" ]; then
    echo "[ERROR] Frontend not built yet. Please run ./install.sh first."
    exit 1
fi

# Activate venv
# shellcheck disable=SC1091
source "$BACKEND_DIR/venv/bin/activate"

echo "Starting Marstek Dashboard..."
echo "Dashboard available at: http://localhost:5000"
echo "Press Ctrl+C to stop."
echo ""

# Open browser after 2 seconds (works on Linux + macOS)
(
    sleep 2
    if command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:5000" &>/dev/null &
    elif command -v open &>/dev/null; then
        open "http://localhost:5000" &>/dev/null &
    fi
) &

cd "$BACKEND_DIR"
python app.py
