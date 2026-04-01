#!/usr/bin/with-contenv bashio
# ==============================================================================
# SmartMarstek – Home Assistant add-on startup script
# ==============================================================================

# ── Log level (from add-on Configuration tab) ─────────────────────────────
LOG_LEVEL=$(bashio::config 'log_level' 'info')
bashio::log.level "${LOG_LEVEL}"
export MARSTEK_LOG_LEVEL="${LOG_LEVEL}"

bashio::log.info "Starting SmartMarstek v$(bashio::addon.version)..."

# ── Persistent data directory ─────────────────────────────────────────────
export MARSTEK_DATA_DIR="/data"
export MARSTEK_FRONTEND_DIST="/app/frontend/dist"
mkdir -p "${MARSTEK_DATA_DIR}"

# ── InfluxDB: auto-discover HA add-on via Supervisor ──────────────────────
USE_HA_INFLUX=$(bashio::config 'influx_use_ha_addon' 'false')
if bashio::var.true "${USE_HA_INFLUX}"; then
    bashio::log.info "influx_use_ha_addon enabled – discovering InfluxDB via Supervisor..."
    if bashio::services.available "influxdb"; then
        export INFLUX_ADDON_HOST=$(bashio::services "influxdb" "host")
        export INFLUX_ADDON_PORT=$(bashio::services "influxdb" "port")
        export INFLUX_ADDON_SSL=$(bashio::services  "influxdb" "ssl")
        export INFLUX_ADDON_USERNAME=$(bashio::services "influxdb" "username")
        export INFLUX_ADDON_PASSWORD=$(bashio::services "influxdb" "password")
        bashio::log.info "InfluxDB add-on found at ${INFLUX_ADDON_HOST}:${INFLUX_ADDON_PORT}"
    else
        bashio::log.warning "influx_use_ha_addon is true but no InfluxDB service found via Supervisor."
        bashio::log.warning "Make sure the InfluxDB add-on is installed and running."
    fi
fi

# ── Apply add-on config tab settings to data JSON files ──────────────────
bashio::log.info "Applying add-on configuration..."
python3 /app/setup_config.py

# ── Start Flask backend ────────────────────────────────────────────────────
bashio::log.info "Starting backend on port 5000..."
cd /app/backend
exec python3 app.py
