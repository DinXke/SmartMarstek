#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

ok()  { echo -e "${GREEN}[OK]${RESET}  $*"; }
err() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
hdr() { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="smartmarstek_backup_${TIMESTAMP}"
BACKUP_PATH="${SCRIPT_DIR}/${BACKUP_DIR}"
ARCHIVE_NAME="${BACKUP_DIR}.tar.gz"

hdr "SmartMarstek Backup Script"
hdr "════════════════════════════════════════"

# Check if docker is running
if ! docker ps &>/dev/null; then
    err "Docker is not running. Please start Docker first."
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_PATH"
ok "Created backup directory: $BACKUP_PATH"

# ─────────────────────────────────────────────────────────────────────────────
# Backup InfluxDB data
# ─────────────────────────────────────────────────────────────────────────────

hdr "Backing up InfluxDB..."

# Check if InfluxDB container is running
if docker ps | grep -q marstek-influxdb; then
    # Export InfluxDB data using influx CLI inside container
    docker exec marstek-influxdb influx backup \
        /var/lib/influxdb2/backup 2>/dev/null || true

    # Copy backup to our backup directory
    docker cp marstek-influxdb:/var/lib/influxdb2/backup "$BACKUP_PATH/influxdb_backup" 2>/dev/null || {
        warn "Could not backup InfluxDB via CLI, attempting filesystem backup..."
        docker cp marstek-influxdb:/var/lib/influxdb2 "$BACKUP_PATH/influxdb_data" 2>/dev/null || true
    }

    ok "InfluxDB data backed up"
else
    warn "InfluxDB container not running, skipping InfluxDB backup"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Backup configuration files
# ─────────────────────────────────────────────────────────────────────────────

hdr "Backing up configuration files..."

if [ -d "$SCRIPT_DIR/data" ]; then
    cp -r "$SCRIPT_DIR/data" "$BACKUP_PATH/data" 2>/dev/null || true
    ok "Data files backed up"
fi

if [ -d "$SCRIPT_DIR/config" ]; then
    cp -r "$SCRIPT_DIR/config" "$BACKUP_PATH/config" 2>/dev/null || true
    ok "Config files backed up"
fi

if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$BACKUP_PATH/.env" 2>/dev/null || true
    ok "Environment configuration backed up"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Create archive and verify
# ─────────────────────────────────────────────────────────────────────────────

hdr "Creating compressed archive..."

cd "$SCRIPT_DIR"
tar -czf "$ARCHIVE_NAME" "$BACKUP_DIR" 2>/dev/null

if [ ! -f "$ARCHIVE_NAME" ]; then
    err "Failed to create backup archive"
    rm -rf "$BACKUP_PATH"
    exit 1
fi

ARCHIVE_SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)
ok "Archive created: $ARCHIVE_NAME ($ARCHIVE_SIZE)"

# Cleanup temp directory
rm -rf "$BACKUP_PATH"
ok "Cleanup complete"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

hdr "╭─────────────────────────────────────────╮"
hdr "│   Backup Complete!                     │"
hdr "├─────────────────────────────────────────┤"
echo -e "  ${CYAN}Backup file:${RESET}  $ARCHIVE_NAME"
echo -e "  ${CYAN}Size:${RESET}          $ARCHIVE_SIZE"
echo -e "  ${CYAN}Location:${RESET}      $(pwd)/$ARCHIVE_NAME"
echo ""
echo -e "  ${BOLD}To restore:${RESET}"
echo -e "    ./restore.sh $ARCHIVE_NAME"
hdr "╰─────────────────────────────────────────╯"
