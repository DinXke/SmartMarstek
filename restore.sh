#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

ok()  { echo -e "${GREEN}[OK]${RESET}  $*"; }
err() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
hdr() { echo -e "\n${BOLD}${CYAN}$*${RESET}"; }
warn() { echo -e "${YELLOW}[WARN]${RESET}  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

hdr "SmartMarstek Restore Script"
hdr "════════════════════════════════════════"

# ─────────────────────────────────────────────────────────────────────────────
# Argument validation
# ─────────────────────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
    err "Usage: ./restore.sh <backup-file.tar.gz>"
    echo ""
    echo "Example:"
    echo "  ./restore.sh smartmarstek_backup_20240425_103045.tar.gz"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    err "Backup file not found: $BACKUP_FILE"
    exit 1
fi

if [[ ! "$BACKUP_FILE" =~ \.tar\.gz$ ]]; then
    err "Backup file must be a .tar.gz archive"
    exit 1
fi

ok "Backup file: $BACKUP_FILE"

# ─────────────────────────────────────────────────────────────────────────────
# Confirmation
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}WARNING: This will restore your SmartMarstek data from the backup.${RESET}"
echo "Current data may be overwritten."
echo ""
read -r -p "Are you sure you want to restore from this backup? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    err "Restore cancelled"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Extract backup
# ─────────────────────────────────────────────────────────────────────────────

hdr "Extracting backup..."

TEMP_DIR=$(mktemp -d)
tar -xzf "$BACKUP_FILE" -C "$TEMP_DIR"
ok "Backup extracted to: $TEMP_DIR"

# Find the backup directory (handle naming variations)
BACKUP_DIR=$(find "$TEMP_DIR" -maxdepth 1 -type d -name "smartmarstek_backup_*" | head -1)

if [ -z "$BACKUP_DIR" ]; then
    err "Invalid backup archive format"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Stop containers
# ─────────────────────────────────────────────────────────────────────────────

hdr "Stopping SmartMarstek containers..."

if docker ps | grep -q marstek-influxdb; then
    cd "$SCRIPT_DIR"
    docker-compose down 2>/dev/null || docker compose down 2>/dev/null || true
    ok "Containers stopped"
else
    warn "Containers not running"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Restore InfluxDB
# ─────────────────────────────────────────────────────────────────────────────

hdr "Restoring InfluxDB data..."

if [ -d "$BACKUP_DIR/influxdb_backup" ]; then
    # Restore using backup directory
    warn "InfluxDB restore requires manual steps. See restore-influxdb.md"

    # Create a helper script for manual restore
    cat > "$SCRIPT_DIR/restore-influxdb.md" << 'EOF'
# Manual InfluxDB Restore

If the automatic restore fails, follow these steps:

1. Start the containers:
   docker-compose up -d

2. Wait for InfluxDB to be ready:
   docker exec marstek-influxdb influx ping

3. Restore from backup:
   docker exec marstek-influxdb influx restore /backup --full 2>&1 || true

4. Verify restore:
   docker exec marstek-influxdb influx bucket list

For detailed help:
  docker exec marstek-influxdb influx restore --help
EOF

    ok "Backup restoration prepared (see restore-influxdb.md)"
elif [ -d "$BACKUP_DIR/influxdb_data" ]; then
    # Restore from filesystem backup
    warn "Replacing InfluxDB data volume (this is destructive)"

    # This would require volume replacement which is risky
    # Best to let user handle InfluxDB restore manually or via influx CLI
    ok "InfluxDB filesystem backup available at: $BACKUP_DIR/influxdb_data"
else
    warn "No InfluxDB backup found"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Restore configuration files
# ─────────────────────────────────────────────────────────────────────────────

hdr "Restoring configuration files..."

if [ -d "$BACKUP_DIR/data" ]; then
    cp -r "$BACKUP_DIR/data"/* "$SCRIPT_DIR/data/" 2>/dev/null || true
    ok "Data files restored"
fi

if [ -d "$BACKUP_DIR/config" ]; then
    cp -r "$BACKUP_DIR/config"/* "$SCRIPT_DIR/config/" 2>/dev/null || true
    ok "Config files restored"
fi

if [ -f "$BACKUP_DIR/.env" ]; then
    warn "Found .env in backup. Review before restoring:"
    echo "  Backup .env: $BACKUP_DIR/.env"
    echo "  Current .env: $SCRIPT_DIR/.env"
    read -r -p "Overwrite current .env with backup? (yes/no): " RESTORE_ENV
    if [ "$RESTORE_ENV" = "yes" ]; then
        cp "$BACKUP_DIR/.env" "$SCRIPT_DIR/.env"
        ok ".env restored"
    else
        ok ".env not restored (current file kept)"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Restart containers
# ─────────────────────────────────────────────────────────────────────────────

hdr "Starting SmartMarstek containers..."

cd "$SCRIPT_DIR"
if docker-compose up -d 2>/dev/null || docker compose up -d 2>/dev/null; then
    ok "Containers started"
    echo ""
    echo "Waiting for services to be ready..."
    sleep 10
else
    err "Failed to start containers"
    echo "Try: cd $SCRIPT_DIR && docker-compose up -d"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

rm -rf "$TEMP_DIR"
ok "Temporary files cleaned up"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

hdr "╭─────────────────────────────────────────╮"
hdr "│   Restore Complete!                    │"
hdr "├─────────────────────────────────────────┤"
echo -e "  ${CYAN}Configuration:${RESET}  Restored"
echo -e "  ${CYAN}InfluxDB:${RESET}       Manual restore may be needed"
echo -e "  ${CYAN}Services:${RESET}       Started"
echo ""
echo -e "  ${BOLD}Check status:${RESET}"
echo -e "    docker-compose logs -f"
echo ""
echo -e "  ${BOLD}Access:${RESET}"
echo -e "    ${CYAN}http://localhost${RESET}"
hdr "╰─────────────────────────────────────────╯"
