# Changelog

## [1.4.0] - 2026-04-02

### Added
- InfluxDB as a live source option in "Vermogensstroom bronnen": configured slots
  (Zonnepanelen, Net, Thuisverbruik, Batterij vermogen, Batterij SOC) now appear
  as a 📊 InfluxDB group alongside ESPHome / HomeWizard / HA entities
- New `/api/influx/live-slots` endpoint: returns the latest value per configured
  InfluxDB slot (bat_soc averaged, others summed)
- `HomeFlow` resolves and polls InfluxDB live values every 10 s when influx
  sources are selected

## [1.3.0] - 2026-04-02

### Fixed
- InfluxDB scan 401: masked password placeholder (`••••••••`) was sent to
  InfluxDB instead of the real stored password — now falls back to the
  stored secret when the UI sends a masked value (same logic as the save endpoint)

## [1.2.0] - 2026-04-01

### Fixed
- InfluxDB v1 Basic Auth encoding error: `'latin-1' codec can't encode characters`
  — credentials are now encoded as UTF-8 + base64 instead of relying on
  `requests`' default latin-1 path

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
