# Changelog

## [1.1.0] - 2026-04-01

### Added
- InfluxDB auto-discovery: `influx_use_ha_addon` option connects to the HA
  InfluxDB add-on via Supervisor service discovery (no URL needed)
- Full Configuration tab: HA URL/token, ENTSO-E key, InfluxDB connection,
  timezone, log level — all settable from the HA add-on UI
- Sensitive fields (token, API keys, passwords) masked with *** in Config tab
- `setup_config.py`: reads `/data/options.json` at startup and writes
  individual settings JSON files so web UI and CLI mode both work

### Fixed
- HA ingress compatibility: Flask now injects `<base href="...">` tag based
  on `X-Ingress-Path` header so the app works via Cloudflare/remote access
- All 49 frontend API calls converted to relative paths (`api/...` instead
  of `/api/...`) so they route correctly through the HA ingress proxy
- `vite.config.js`: `base: "./"` so bundled assets use relative paths
- Base Docker image updated to `ghcr.io/home-assistant/*-base-python:3.13-alpine3.21`
  (previous `3.12` tag does not exist in the HA registry)
- Added missing `pytz` dependency (required by `python-frank-energie`)
- Dropped deprecated architectures armhf/armv7/i386

## [1.0.0] - 2026-04-01

### Added
- Initial release
- ENTSO-E day-ahead electricity price integration with charge strategy planner
- Solar forecast integration (Open-Meteo)
- Home Assistant sensor history support for consumption profile
- InfluxDB v1/v2 consumption history support
- ESPHome battery control (mode select, force charge/discharge)
- Automation toggle: auto-applies strategy actions to battery every minute
- Home Assistant add-on with ingress sidebar panel
- Configuration tab: HA token, ENTSO-E key, InfluxDB connection
- Multi-arch Docker image (aarch64, amd64)
