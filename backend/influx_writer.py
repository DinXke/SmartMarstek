"""
influx_writer.py – Background thread that polls configured sensors every 30 s
and writes them as time-series points to InfluxDB 2.x.

Sensors collected
-----------------
  ESPHome   : GET /api/states  (direct HTTP, independent of frontend SSE)
  HomeWizard: GET /api/homewizard/data  (internal call reusing app logic)
  HA        : POST /api/ha/poll         (internal call reusing app logic)
  Flow slots: resolved from marstek_flow_cfg.json via the same logic as HomeFlow

The measurement written is ``energy_flow`` with fields:
  solar_w, net_w, bat_w, bat_soc, house_w, ev_w,
  voltage_l1, voltage_l2, voltage_l3
and a tag  source=resolved|fallback.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("influx_writer")

# ---------------------------------------------------------------------------
# InfluxDB connection settings  (matches docker-compose defaults)
# ---------------------------------------------------------------------------

INFLUX_URL    = os.environ.get("INFLUX_URL",    "http://localhost:8086")
INFLUX_TOKEN  = os.environ.get("INFLUX_TOKEN",  "marstek-influx-token-local")
INFLUX_ORG    = os.environ.get("INFLUX_ORG",    "marstek")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "energy")

WRITE_INTERVAL = 30   # seconds between writes

# ---------------------------------------------------------------------------
# Lazy InfluxDB client (don't crash if influxdb-client is not installed yet)
# ---------------------------------------------------------------------------

_write_api = None
_influx_ok  = False


def _get_write_api():
    global _write_api, _influx_ok
    if _write_api is not None:
        return _write_api
    try:
        from influxdb_client import InfluxDBClient, WriteOptions  # type: ignore
        from influxdb_client.client.write_api import SYNCHRONOUS   # type: ignore
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        _write_api = client.write_api(write_options=SYNCHRONOUS)
        _influx_ok = True
        log.info("InfluxDB connected  url=%s  org=%s  bucket=%s", INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET)
    except Exception as exc:
        log.warning("InfluxDB not available: %s", exc)
        _write_api = None
    return _write_api


# ---------------------------------------------------------------------------
# Flow-config resolution (mirrors HomeFlow / EnergyMap logic in Python)
# ---------------------------------------------------------------------------

FLOW_CFG_FILE = os.path.join(os.path.dirname(__file__), "flow_cfg.json")


def _load_flow_cfg() -> dict:
    try:
        with open(FLOW_CFG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = {}
        for key, val in raw.items():
            if isinstance(val, list):
                cfg[key] = val
            elif isinstance(val, dict):
                cfg[key] = [val]
        return cfg
    except Exception:
        return {}


def _poll_esphome(devices: dict) -> dict:
    """
    Poll each ESPHome device via GET /events (SSE stream) and return
    {device_id: {sensor_key: value}}.
    Reads the initial state burst (until 'ping' event) then closes.
    Sensor keys mirror DeviceCard: batPower, acPower, soc, acVoltage, l1V, l2V, l3V.
    """
    try:
        import requests as _r
        import json as _json
    except ImportError:
        return {}

    # (terms_that_must_all_appear_in_name, target_key)
    # Matched against lowercased entity name with punctuation→space
    NAME_MAP = [
        (["state", "charge"],   "soc"),
        (["battery", "soc"],    "soc"),
        (["bat", "soc"],        "soc"),
        (["battery", "power"],  "batPower"),
        (["bat", "power"],      "batPower"),
        (["ac", "power"],       "acPower"),
        (["grid", "power"],     "acPower"),
        (["ac", "voltage"],     "acVoltage"),
        (["l1", "voltage"],     "l1V"),
        (["l2", "voltage"],     "l2V"),
        (["l3", "voltage"],     "l3V"),
        (["voltage", "l1"],     "l1V"),
        (["voltage", "l2"],     "l2V"),
        (["voltage", "l3"],     "l3V"),
    ]

    def _map_name(entity_id: str) -> Optional[str]:
        slash = entity_id.find("/")
        raw   = entity_id[slash + 1:] if slash >= 0 else entity_id
        name  = raw.lower().replace("_", " ").replace(".", " ").replace("-", " ")
        for terms, key in NAME_MAP:
            if all(t in name for t in terms):
                return key
        return None

    result = {}
    for dev_id, dev in devices.items():
        ip, port = dev.get("ip"), dev.get("port", 6052)
        if not ip:
            continue
        vals: dict = {}
        try:
            with _r.get(
                f"http://{ip}:{port}/events",
                stream=True,
                timeout=(5, 6),   # 5 s connect, 6 s read (ping arrives quickly)
                headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
            ) as resp:
                resp.raise_for_status()
                current_event = None
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if raw_line.startswith("event:"):
                        current_event = raw_line[6:].strip()
                        if current_event == "ping":
                            break   # initial state burst complete
                    elif raw_line.startswith("data:") and current_event == "state":
                        try:
                            data = _json.loads(raw_line[5:].strip())
                            key  = _map_name(data.get("id", ""))
                            if key:
                                v = data.get("value")
                                if v is None:
                                    # parse numeric prefix from "100.0 %" etc.
                                    try:
                                        v = float(str(data.get("state", "")).split()[0])
                                    except Exception:
                                        pass
                                if v is not None:
                                    vals[key] = float(v)
                        except Exception:
                            pass
        except Exception as exc:
            log.debug("ESPHome SSE poll failed  dev=%s  err=%s", dev_id, exc)
        if vals:
            result[dev_id] = vals
            log.debug("ESPHome SSE  dev=%s  fields=%s", dev_id, list(vals.keys()))
    return result


def _resolve_slot(key: str, cfg: dict, esphome_map: dict,
                  hw_data: Optional[dict], ha_data: dict) -> Optional[float]:
    """Resolve a flow slot → numeric value (mirrors JS resolveSlot)."""
    entries = cfg.get(key)
    if not entries:
        return None
    if not isinstance(entries, list):
        entries = [entries]

    is_avg = (key == "bat_soc")
    total, count = None, 0

    for sc in entries:
        source    = sc.get("source")
        device_id = sc.get("device_id")
        sensor    = sc.get("sensor")
        invert    = sc.get("invert", False)

        v = None
        if source == "esphome":
            v = esphome_map.get(device_id, {}).get(sensor)
        elif source == "homewizard":
            dev = next((d for d in (hw_data or {}).get("devices", []) if d["id"] == device_id), None)
            v = dev["sensors"].get(sensor, {}).get("value") if dev else None
        elif source == "homeassistant":
            entry = ha_data.get(sensor)
            v = entry.get("value") if entry else None

        if v is not None:
            total = (total or 0.0) + (-v if invert else v)
            count += 1

    if total is None:
        return None
    return total / count if is_avg and count else total


# ---------------------------------------------------------------------------
# Main collection + write cycle
# ---------------------------------------------------------------------------

def _collect_and_write(app_context_fn):
    """
    One collection cycle.  app_context_fn() returns a dict with:
      devices, hw_data, ha_data, flow_cfg
    fetched inside the Flask app context.
    """
    try:
        ctx = app_context_fn()
    except Exception as exc:
        log.warning("collect context error: %s", exc)
        return

    devices  = ctx.get("devices", {})
    hw_data  = ctx.get("hw_data")
    ha_data  = ctx.get("ha_data", {})
    flow_cfg = ctx.get("flow_cfg", {})

    esphome_map = _poll_esphome(devices)

    SLOT_ORDER = ["solar_power", "net_power", "bat_power", "bat_soc",
                  "ev_power", "voltage_l1", "voltage_l2", "voltage_l3"]
    SLOT_FIELDS = {
        "solar_power": "solar_w",
        "net_power":   "net_w",
        "bat_power":   "bat_w",
        "bat_soc":     "bat_soc",
        "ev_power":    "ev_w",
        "voltage_l1":  "voltage_l1",
        "voltage_l2":  "voltage_l2",
        "voltage_l3":  "voltage_l3",
    }

    fields = {}
    for slot_key in SLOT_ORDER:
        val = _resolve_slot(slot_key, flow_cfg, esphome_map, hw_data, ha_data)
        if val is not None:
            fields[SLOT_FIELDS[slot_key]] = float(val)

    # house_w derived
    solar = fields.get("solar_w", 0.0)
    net   = fields.get("net_w", 0.0)   # positive = import
    bat   = fields.get("bat_w", 0.0)   # positive = discharge
    ev    = fields.get("ev_w", 0.0)
    # house = solar + bat_discharge - net_export + net_import - ev
    # net positive=import: house = solar + bat - (-net) ... simplify:
    # From the JS: housePower = batDisplay - netDisplay + solar - ev
    # netDisplay = -netRaw (positive=export). Here net_w is positive=import.
    # so netDisplay = -net_w
    # house = bat_w - (-net_w) + solar - ev = bat_w + net_w + solar - ev
    if any(k in fields for k in ("solar_w", "net_w", "bat_w")):
        fields["house_w"] = bat + net + solar - ev

    if not fields:
        log.debug("No fields to write – sensors not configured/reachable")
        return

    write_api = _get_write_api()
    if write_api is None:
        return

    try:
        from influxdb_client import Point  # type: ignore
        p = Point("energy_flow").tag("source", "marstek")
        for k, v in fields.items():
            p = p.field(k, v)
        p = p.time(datetime.now(timezone.utc))
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
        log.debug("InfluxDB write OK  fields=%s", list(fields.keys()))
    except Exception as exc:
        log.warning("InfluxDB write error: %s", exc)


# ---------------------------------------------------------------------------
# Background thread entry point
# ---------------------------------------------------------------------------

def start_background_writer(app_context_fn, interval: int = WRITE_INTERVAL):
    """
    Spawn a daemon thread that calls _collect_and_write every `interval` seconds.
    app_context_fn must be callable and return the context dict.
    """
    def _loop():
        log.info("InfluxDB background writer started  interval=%ds", interval)
        while True:
            _collect_and_write(app_context_fn)
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="influx-writer")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Query helpers (used by strategy endpoint)
# ---------------------------------------------------------------------------

def query_avg_hourly_consumption(days: int = 21) -> list[dict]:
    """
    Return average consumption per hour-of-day [0..23]
    based on the last `days` days of house_w data.
    Returns list of 24 dicts: {hour: int, avg_wh: float}.
    """
    write_api = _get_write_api()
    if write_api is None:
        return []

    try:
        from influxdb_client import InfluxDBClient  # type: ignore
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        query_api = client.query_api()

        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "energy_flow" and r._field == "house_w")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({{r with hour: int(v: r._time) / 3600000000000 % 24}}))
  |> group(columns: ["hour"])
  |> mean(column: "_value")
"""
        tables = query_api.query(flux, org=INFLUX_ORG)
        result = []
        for table in tables:
            for record in table.records:
                hour = record.values.get("hour")
                val  = record.get_value()
                if hour is not None and val is not None:
                    result.append({"hour": int(hour), "avg_wh": float(val)})
        result.sort(key=lambda x: x["hour"])
        return result
    except Exception as exc:
        log.warning("InfluxDB hourly query error: %s", exc)
        return []


def query_day_actuals(date_str: str, tz_name: str = "Europe/Brussels") -> dict:
    """
    Return actual hourly energy-flow data for a specific calendar date from InfluxDB.
    Keys are hour integers (0-23); values are dicts with available fields.
    Used by the strategy historical-day view.
    """
    write_api = _get_write_api()
    if write_api is None:
        return {}
    try:
        from zoneinfo import ZoneInfo
        from datetime import date as _date, datetime as _dt
        tz = ZoneInfo(tz_name)
        d = _date.fromisoformat(date_str)
        day_start = _dt(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
        day_end   = _dt(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
        start_utc = day_start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc   = day_end.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

        from influxdb_client import InfluxDBClient  # type: ignore
        client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        query_api = client.query_api()

        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {start_utc}, stop: {end_utc})
  |> filter(fn: (r) => r._measurement == "energy_flow")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        tables = query_api.query(flux, org=INFLUX_ORG)
        result: dict[int, dict] = {}
        for table in tables:
            for record in table.records:
                t    = record.get_time().astimezone(tz)
                hour = t.hour
                row  = {}
                for field in ("solar_w", "net_w", "bat_w", "bat_soc", "house_w", "ev_w"):
                    v = record.values.get(field)
                    if v is not None:
                        row[field] = round(float(v), 1)
                if row:
                    result[hour] = row
        return result
    except Exception as exc:
        log.warning("InfluxDB day actuals error (%s): %s", date_str, exc)
        return {}


def query_recent_points(hours: int = 24) -> list[dict]:
    """
    Return last `hours` hours of energy_flow data as list of dicts.
    Used for the live chart on the strategy page.
    """
    write_api = _get_write_api()
    if write_api is None:
        return []

    try:
        from influxdb_client import InfluxDBClient  # type: ignore
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        query_api = client.query_api()

        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "energy_flow")
  |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        tables = query_api.query(flux, org=INFLUX_ORG)
        result = []
        for table in tables:
            for record in table.records:
                row = {"time": record.get_time().isoformat()}
                for field in ("solar_w","net_w","bat_w","bat_soc","house_w","ev_w"):
                    v = record.values.get(field)
                    if v is not None:
                        row[field] = round(float(v), 1)
                result.append(row)
        result.sort(key=lambda x: x["time"])
        return result
    except Exception as exc:
        log.warning("InfluxDB recent query error: %s", exc)
        return []
