import ipaddress
import json
import logging
import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, quote
from urllib.request import urlopen, Request
from urllib.error import URLError

from influx_writer import (start_background_writer, query_avg_hourly_consumption,
                           query_recent_points, query_day_actuals)
from strategy import (load_strategy_settings, save_strategy_settings,
                      build_plan, split_days)

import requests as _req  # aliased to avoid clash with flask.request
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # v2 uses self-signed cert
from flask import Flask, Response, jsonify, request, send_from_directory, abort, stream_with_context
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
# CODE_DIR = location of this .py file (code, never changes)
# DATA_DIR = where settings/data files are stored.
#            Set MARSTEK_DATA_DIR env var to override (used by HA add-on → /data).
_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.environ.get("MARSTEK_DATA_DIR", _CODE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, "marstek.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),                         # stdout (visible in terminal)
        logging.FileHandler(LOG_FILE, encoding="utf-8"), # persistent log file
    ],
)
log = logging.getLogger("marstek")

app = Flask(__name__, static_folder=None)
CORS(app)


@app.before_request
def _log_request():
    if request.path.startswith("/api/"):
        log.debug("→ %s %s  args=%s", request.method, request.path, dict(request.args))

# ---------------------------------------------------------------------------
# Device storage
# ---------------------------------------------------------------------------
BASE_DIR      = DATA_DIR   # legacy alias – data files
DATA_FILE     = os.path.join(DATA_DIR,   "devices.json")
FRONTEND_DIST = os.environ.get(
    "MARSTEK_FRONTEND_DIST",
    os.path.join(_CODE_DIR, "..", "frontend", "dist"),
)


def load_devices() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_devices(devices: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2)


# ---------------------------------------------------------------------------
# ESPHome helpers
# ---------------------------------------------------------------------------

def send_esphome_command(ip: str, port: int, domain: str, name: str, value: str) -> dict:
    """
    Send a command to an ESPHome entity identified by its friendly name.
    ESPHome web server v3 uses the entity name (URL-encoded) in REST paths:
      SELECT  → POST /select/{name}/set?option={value}
      NUMBER  → POST /number/{name}/set?value={value}
    """
    encoded_name = quote(name, safe="")

    if domain == "select":
        path = f"/select/{encoded_name}/set"
        params = urlencode({"option": value})
    elif domain == "number":
        path = f"/number/{encoded_name}/set"
        params = urlencode({"value": value})
    else:
        return {"ok": False, "error": f"Unsupported domain: {domain}"}

    url = f"http://{ip}:{port}{path}?{params}"
    try:
        req = Request(url, method="POST", data=b"")
        req.add_header("Content-Length", "0")
        with urlopen(req, timeout=5) as resp:
            return {"ok": True, "status": resp.status}
    except URLError as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# API routes – devices
# ---------------------------------------------------------------------------

@app.route("/api/devices", methods=["GET"])
def list_devices():
    return jsonify(list(load_devices().values()))


@app.route("/api/devices", methods=["POST"])
def add_device():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    ip = (body.get("ip") or "").strip()
    port = int(body.get("port") or 80)

    if not name or not ip:
        return jsonify({"error": "name and ip are required"}), 400

    devices = load_devices()
    device_id = str(uuid.uuid4())
    device = {"id": device_id, "name": name, "ip": ip, "port": port}
    devices[device_id] = device
    save_devices(devices)
    return jsonify(device), 201


@app.route("/api/devices/<device_id>", methods=["PUT"])
def update_device(device_id):
    devices = load_devices()
    if device_id not in devices:
        return jsonify({"error": "Device not found"}), 404

    body = request.get_json(force=True)
    device = devices[device_id]
    if "name" in body and body["name"].strip():
        device["name"] = body["name"].strip()
    if "ip" in body and body["ip"].strip():
        device["ip"] = body["ip"].strip()
    if "port" in body:
        device["port"] = int(body["port"])

    save_devices(devices)
    return jsonify(device)


@app.route("/api/devices/<device_id>", methods=["DELETE"])
def delete_device(device_id):
    devices = load_devices()
    if device_id not in devices:
        return jsonify({"error": "Device not found"}), 404
    del devices[device_id]
    save_devices(devices)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API routes – ESPHome data & commands
# ---------------------------------------------------------------------------

@app.route("/api/devices/<device_id>/stream")
def stream_device(device_id):
    """
    Proxy the ESPHome /events SSE stream to the browser.
    ESPHome sends all current entity states on connect, then live updates.
    Uses requests (handles chunked transfer encoding automatically).
    Auto-reconnects if the connection drops.
    """
    devices = load_devices()
    if device_id not in devices:
        return jsonify({"error": "Device not found"}), 404

    device = devices[device_id]
    ip = device["ip"]
    port = device["port"]

    def generate():
        log.info("SSE stream open  device=%s  ip=%s:%s", device_id, ip, port)
        while True:
            try:
                with _req.get(
                    f"http://{ip}:{port}/events",
                    stream=True,
                    timeout=(5, 65),  # 5s connect, 65s read (ESPHome pings every 30s)
                    headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
                ) as resp:
                    resp.raise_for_status()
                    log.debug("SSE connected  device=%s  status=%s", device_id, resp.status_code)
                    for chunk in resp.iter_content(chunk_size=512):
                        if chunk:
                            yield chunk
            except GeneratorExit:
                log.info("SSE stream closed  device=%s", device_id)
                return
            except Exception as exc:
                log.warning("SSE error  device=%s  err=%s — reconnecting in 5 s", device_id, exc)
                msg = f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                yield msg.encode()
                time.sleep(5)  # wait before reconnecting

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/devices/<device_id>/command", methods=["POST"])
def send_command(device_id):
    devices = load_devices()
    if device_id not in devices:
        return jsonify({"error": "Device not found"}), 404

    device = devices[device_id]
    body = request.get_json(force=True)

    # entity.id in the frontend is "domain/Entity Name" (ESPHome v3 format)
    domain = (body.get("domain") or "").strip()
    name = (body.get("name") or "").strip()    # friendly name, e.g. "Marstek User Work Mode"
    value = str(body.get("value") or "").strip()

    if not domain or not name or value == "":
        return jsonify({"error": "domain, name, and value are required"}), 400

    result = send_esphome_command(device["ip"], device["port"], domain, name, value)
    return jsonify(result), 200 if result.get("ok") else 502


# ---------------------------------------------------------------------------
# Frank Energie – prices + authentication (via python-frank-energie package)
# ---------------------------------------------------------------------------

import asyncio
import xml.etree.ElementTree as ET
import re as _re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from python_frank_energie import FrankEnergie

FRANK_SESSION_FILE = os.path.join(BASE_DIR, "frank_session.json")
_price_cache: dict = {}   # keyed by ISO date string


def _country_from_token(token: str) -> str | None:
    """Decode the JWT payload (no signature check) to extract countryCode."""
    try:
        import base64
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("countryCode") or None
    except Exception:
        return None


def _frank_session() -> dict:
    if os.path.exists(FRANK_SESSION_FILE):
        with open(FRANK_SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_frank_session(data: dict) -> None:
    with open(FRANK_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


FRANK_API_URL = "https://graphql.frankenergie.nl/"

_QUERY_NL = """
query MarketPrices($startDate: String!, $endDate: String!) {
  marketPricesElectricity(startDate: $startDate, endDate: $endDate) {
    from till marketPrice marketPriceTax sourcingMarkupPrice energyTaxPrice perUnit
  }
}
"""

_QUERY_NL_AUTH = """
query CustomerPrices($startDate: String!, $endDate: String!) {
  pricesElectricity(startDate: $startDate, endDate: $endDate) {
    from till marketPrice marketPriceTax sourcingMarkupPrice energyTaxPrice perUnit
  }
}
"""

_QUERY_BE = """
query MarketPrices($date: String!) {
  marketPrices(date: $date) {
    electricityPrices {
      from till marketPrice marketPriceTax sourcingMarkupPrice energyTaxPrice perUnit
    }
  }
}
"""


def _frank_request(query: str, variables: dict, auth_token: str | None = None,
                   country: str = "NL") -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-graphql-client-name": "frank-app",
        "x-graphql-client-version": "4.13.3",
        "skip-graphcdn": "1",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if country == "BE":
        headers["x-country"] = "BE"
    log.debug("Frank API  url=%s  country=%s  vars=%s", FRANK_API_URL, country, variables)
    resp = _req.post(FRANK_API_URL,
                     json={"query": query, "variables": variables},
                     headers=headers, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    log.debug("Frank API response keys: %s", list(body.keys()))
    if "errors" in body:
        msgs = [e.get("message", "?") for e in body["errors"]]
        log.error("Frank API errors: %s", msgs)
        raise ValueError("; ".join(msgs))
    return body.get("data", {})


def _fetch_prices(auth_token: str | None, start: date, end: date,
                  country: str = "NL") -> list:
    """Return a list of hourly price dicts for [start, end)."""
    if country == "BE":
        data = _frank_request(_QUERY_BE, {"date": str(start)},
                               auth_token=auth_token, country="BE")
        mp = data.get("marketPrices") or {}
        rows = mp.get("electricityPrices") or []
        log.debug("BE price rows: %d  raw keys: %s", len(rows), list(mp.keys()))
    elif auth_token:
        try:
            data = _frank_request(_QUERY_NL_AUTH,
                                   {"startDate": str(start), "endDate": str(end)},
                                   auth_token=auth_token)
            rows = data.get("pricesElectricity") or []
            if not rows:           # fall back to public
                raise ValueError("empty personalized response")
        except Exception as exc:
            log.warning("Personalized prices failed (%s) — falling back to market prices", exc)
            data = _frank_request(_QUERY_NL, {"startDate": str(start), "endDate": str(end)})
            rows = data.get("marketPricesElectricity") or []
    else:
        data = _frank_request(_QUERY_NL, {"startDate": str(start), "endDate": str(end)})
        rows = data.get("marketPricesElectricity") or []

    return rows  # already plain dicts with the keys the frontend expects


@app.route("/api/frank/login", methods=["POST"])
def frank_login():
    body     = request.get_json(force=True)
    email    = (body.get("email")    or "").strip()
    password = (body.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "email and password required"}), 400

    try:
        log.info("Frank Energie login  email=%s", email)

        async def do_login():
            async with FrankEnergie() as fe:
                auth = await fe.login(email, password)
            return auth

        auth = asyncio.run(do_login())

        # Extract country from JWT payload (avoids fe.me() which has a BE bug)
        country = _country_from_token(auth.authToken) or "NL"
        log.info("Country from JWT: %s", country)
        log.info("Frank login OK  email=%s  country=%s", email, country)
        session = {
            "email":        email,
            "authToken":    auth.authToken,
            "refreshToken": auth.refreshToken,
            "country":      country,
            "ts":           int(time.time()),
        }
        _save_frank_session(session)
        _price_cache.clear()
        log.info("Frank login OK  email=%s", email)
        return jsonify({"ok": True, "email": email})
    except Exception as exc:
        log.error("Frank login error: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 502


@app.route("/api/frank/logout", methods=["POST"])
def frank_logout():
    if os.path.exists(FRANK_SESSION_FILE):
        os.remove(FRANK_SESSION_FILE)
    _price_cache.clear()
    return jsonify({"ok": True})


@app.route("/api/frank/status", methods=["GET"])
def frank_status():
    s = _frank_session()
    if s.get("authToken"):
        return jsonify({"loggedIn": True, "email": s.get("email"), "country": s.get("country") or "NL"})
    return jsonify({"loggedIn": False})


@app.route("/api/prices/electricity", methods=["GET"])
def get_electricity_prices():
    today    = date.today()
    tomorrow = today + timedelta(days=1)

    cache_key = today.isoformat()
    cached = _price_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < 1800:
        return jsonify(cached["data"])

    session       = _frank_session()
    auth_token    = session.get("authToken")
    refresh_token = session.get("refreshToken")
    country       = session.get("country") or _country_from_token(auth_token or "") or "NL"

    try:
        log.info("Fetching electricity prices  date=%s  loggedIn=%s  country=%s",
                 today.isoformat(), bool(auth_token), country)
        today_prices = _fetch_prices(auth_token, today, tomorrow, country)
        log.debug("Today prices: %d slots", len(today_prices))

        tomorrow_prices: list = []
        try:
            tomorrow_prices = _fetch_prices(auth_token, tomorrow, tomorrow + timedelta(days=1), country)
            log.debug("Tomorrow prices: %d slots", len(tomorrow_prices))
        except Exception as exc2:
            log.warning("Tomorrow prices unavailable: %s", exc2)

        result = {
            "today":    today_prices,
            "tomorrow": tomorrow_prices,
            "loggedIn": bool(auth_token),
            "email":    session.get("email"),
        }
        _price_cache[cache_key] = {"data": result, "ts": time.time()}
        return jsonify(result)
    except Exception as exc:
        log.error("Prices fetch error: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 502


# ---------------------------------------------------------------------------
# HomeWizard Energy – local API (no authentication required)
# ---------------------------------------------------------------------------

HW_DEVICES_FILE = os.path.join(BASE_DIR, "homewizard_devices.json")

# Metadata for known sensor keys (supports both old v3 and new v4+ field names)
HW_SENSOR_META: dict = {
    # ── Vermogen ───────────────────────────────────────────────────────────
    "power_w":              {"label": "Vermogen totaal",      "unit": "W",     "group": "Vermogen", "power": True},
    "power_l1_w":           {"label": "Vermogen L1",          "unit": "W",     "group": "Vermogen", "power": True},
    "power_l2_w":           {"label": "Vermogen L2",          "unit": "W",     "group": "Vermogen", "power": True},
    "power_l3_w":           {"label": "Vermogen L3",          "unit": "W",     "group": "Vermogen", "power": True},
    "active_power_w":       {"label": "Vermogen totaal",      "unit": "W",     "group": "Vermogen", "power": True},
    "active_power_l1_w":    {"label": "Vermogen L1",          "unit": "W",     "group": "Vermogen", "power": True},
    "active_power_l2_w":    {"label": "Vermogen L2",          "unit": "W",     "group": "Vermogen", "power": True},
    "active_power_l3_w":    {"label": "Vermogen L3",          "unit": "W",     "group": "Vermogen", "power": True},
    # ── Spanning ───────────────────────────────────────────────────────────
    "voltage_l1_v":         {"label": "Spanning L1",          "unit": "V",     "group": "Spanning"},
    "voltage_l2_v":         {"label": "Spanning L2",          "unit": "V",     "group": "Spanning"},
    "voltage_l3_v":         {"label": "Spanning L3",          "unit": "V",     "group": "Spanning"},
    "active_voltage_l1_v":  {"label": "Spanning L1",          "unit": "V",     "group": "Spanning"},
    "active_voltage_l2_v":  {"label": "Spanning L2",          "unit": "V",     "group": "Spanning"},
    "active_voltage_l3_v":  {"label": "Spanning L3",          "unit": "V",     "group": "Spanning"},
    # ── Stroom ─────────────────────────────────────────────────────────────
    "current_l1_a":         {"label": "Stroom L1",            "unit": "A",     "group": "Stroom"},
    "current_l2_a":         {"label": "Stroom L2",            "unit": "A",     "group": "Stroom"},
    "current_l3_a":         {"label": "Stroom L3",            "unit": "A",     "group": "Stroom"},
    "active_current_l1_a":  {"label": "Stroom L1",            "unit": "A",     "group": "Stroom"},
    "active_current_l2_a":  {"label": "Stroom L2",            "unit": "A",     "group": "Stroom"},
    "active_current_l3_a":  {"label": "Stroom L3",            "unit": "A",     "group": "Stroom"},
    # ── Energie totalen ────────────────────────────────────────────────────
    "energy_import_kwh":        {"label": "Import totaal",    "unit": "kWh",   "group": "Totalen"},
    "energy_import_t1_kwh":     {"label": "Import tarief 1",  "unit": "kWh",   "group": "Totalen"},
    "energy_import_t2_kwh":     {"label": "Import tarief 2",  "unit": "kWh",   "group": "Totalen"},
    "energy_export_kwh":        {"label": "Export totaal",    "unit": "kWh",   "group": "Totalen"},
    "energy_export_t1_kwh":     {"label": "Export tarief 1",  "unit": "kWh",   "group": "Totalen"},
    "energy_export_t2_kwh":     {"label": "Export tarief 2",  "unit": "kWh",   "group": "Totalen"},
    "total_power_import_kwh":   {"label": "Import totaal",    "unit": "kWh",   "group": "Totalen"},
    "total_power_import_t1_kwh":{"label": "Import tarief 1",  "unit": "kWh",   "group": "Totalen"},
    "total_power_import_t2_kwh":{"label": "Import tarief 2",  "unit": "kWh",   "group": "Totalen"},
    "total_power_export_kwh":   {"label": "Export totaal",    "unit": "kWh",   "group": "Totalen"},
    "total_power_export_t1_kwh":{"label": "Export tarief 1",  "unit": "kWh",   "group": "Totalen"},
    "total_power_export_t2_kwh":{"label": "Export tarief 2",  "unit": "kWh",   "group": "Totalen"},
    # ── Overig netmeting ───────────────────────────────────────────────────
    "frequency_hz":             {"label": "Frequentie",       "unit": "Hz",    "group": "Overig"},
    "active_frequency_hz":      {"label": "Frequentie",       "unit": "Hz",    "group": "Overig"},
    "apparent_power_va":        {"label": "Schijnbaar verm.", "unit": "VA",    "group": "Overig"},
    "active_apparent_power_va": {"label": "Schijnbaar verm.", "unit": "VA",    "group": "Overig"},
    "reactive_power_var":       {"label": "Reactief verm.",   "unit": "VAr",   "group": "Overig"},
    "active_reactive_power_var":{"label": "Reactief verm.",   "unit": "VAr",   "group": "Overig"},
    "power_factor":             {"label": "Vermogensfactor",  "unit": "",      "group": "Overig"},
    "active_tariff":            {"label": "Actief tarief",    "unit": "",      "group": "Overig"},
    # ── Gas ────────────────────────────────────────────────────────────────
    "total_gas_m3":             {"label": "Gasverbruik",      "unit": "m³",    "group": "Gas"},
    # ── Water ──────────────────────────────────────────────────────────────
    "total_liter_m3":           {"label": "Waterverbruik",    "unit": "m³",    "group": "Water"},
    "active_liter_lpm":         {"label": "Debiet",           "unit": "L/min", "group": "Water"},
    # ── Batterij / stopcontact ─────────────────────────────────────────────
    "state_of_charge_pct":      {"label": "Batterijlading",   "unit": "%",     "group": "Batterij"},
}


def _hw_devices() -> dict:
    try:
        if os.path.exists(HW_DEVICES_FILE):
            with open(HW_DEVICES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        log.error("hw_devices: failed to read %s: %s — returning empty", HW_DEVICES_FILE, exc)
    return {}


def _save_hw_devices(devices: dict) -> None:
    try:
        with open(HW_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=2)
    except Exception as exc:
        log.error("hw_devices: failed to save %s: %s", HW_DEVICES_FILE, exc)


def _hw_fetch(ip: str, path: str, token: str | None = None, timeout: int = 5) -> dict:
    """Fetch from a HomeWizard device. Uses v2 (HTTPS + Bearer) when token is given."""
    if token:
        resp = _req.get(f"https://{ip}{path}", timeout=timeout, verify=False,
                        headers={"Authorization": f"Bearer {token}", "X-Api-Version": "2"})
    else:
        resp = _req.get(f"http://{ip}{path}", timeout=timeout)
    resp.raise_for_status()
    # Detect HTML responses (device returned web UI instead of JSON API)
    ct   = resp.headers.get("Content-Type", "")
    body = resp.text
    if "html" in ct or body.lstrip().startswith("<!"):
        raise ValueError(
            f"Apparaat op {ip} stuurde HTML terug i.p.v. JSON. "
            "Controleer of 'Lokale API' ingeschakeld is in de HomeWizard app: "
            "Energy Socket → selecteer apparaat → ⚙ → Lokale API aan. "
            "P1-meter: Instellingen → Meters → … → Lokale API."
        )
    try:
        return resp.json()
    except Exception:
        snippet = body.strip()[:120].replace("\n", " ")
        raise ValueError(
            f"Apparaat op {ip} stuurde geen geldig JSON "
            f"(Content-Type: {ct!r}). "
            "Controleer of 'Lokale API' ingeschakeld is. "
            f"Ontvangen: {snippet!r}"
        )


def _local_subnet() -> str:
    """Detect the local /24 subnet by probing an external address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"{ip.rsplit('.', 1)[0]}.0/24"
    except Exception:
        return "192.168.1.0/24"


def _hw_probe(ip: str) -> dict | None:
    """Try both API v1 and v2 at an IP. Returns device info dict or None.
    NOTE: api_version is set AFTER **d so our integer (1 or 2) always wins
    over the device's own api_version field (which can be strings like 'v1').
    """
    # v1: plain HTTP, no auth
    try:
        resp = _req.get(f"http://{ip}/api", timeout=1)
        if resp.status_code == 200:
            d = resp.json()
            if "product_type" in d or "product_name" in d:
                return {**d, "ip": ip, "api_version": 1}  # our api_version wins
    except Exception:
        pass
    # v2: HTTPS, self-signed cert, no token needed for /api info endpoint
    try:
        resp = _req.get(f"https://{ip}/api", timeout=1, verify=False,
                        headers={"X-Api-Version": "2"})
        if resp.status_code == 200:
            d = resp.json()
            if "product_type" in d or "product_name" in d:
                return {**d, "ip": ip, "api_version": 2}  # our api_version wins
    except Exception:
        pass
    return None


def _hw_sensor_meta(key: str) -> dict:
    if key in HW_SENSOR_META:
        return HW_SENSOR_META[key]
    # Auto-generate from key name
    label = key.replace("_", " ").title()
    unit  = "W" if key.endswith("_w") else \
            "V" if key.endswith("_v") else \
            "A" if key.endswith("_a") else \
            "kWh" if key.endswith("_kwh") else \
            "Hz" if key.endswith("_hz") else \
            "m³" if key.endswith("_m3") else ""
    return {"label": label, "unit": unit, "group": "Overig"}


@app.route("/api/homewizard/devices", methods=["GET"])
def hw_list_devices():
    return jsonify(list(_hw_devices().values()))


@app.route("/api/homewizard/devices", methods=["POST"])
def hw_add_device():
    body  = request.get_json(force=True) or {}
    ip    = (body.get("ip")    or "").strip()
    name  = (body.get("name")  or "").strip()
    token = (body.get("token") or "").strip() or None
    api_v = int(body.get("api_version") or 1)
    if not ip:
        return jsonify({"error": "IP-adres is vereist"}), 400

    # Auto-probe if version not supplied
    probe = None
    try:
        probe = _hw_probe(ip)
        if probe:
            api_v = probe.get("api_version", api_v)
    except Exception:
        pass

    if not probe:
        # Manual add: trust caller's api_version
        try:
            info = _hw_fetch(ip, "/api", token=token if api_v == 2 else None)
        except Exception as exc:
            return jsonify({"error": f"Apparaat niet bereikbaar: {exc}"}), 502
    else:
        info = probe

    devices   = _hw_devices()
    device_id = str(uuid.uuid4())
    device    = {
        "id":               device_id,
        "name":             name or info.get("product_name", "HomeWizard"),
        "ip":               ip,
        "api_version":      api_v,
        "token":            token,
        "product_type":     info.get("product_type", ""),
        "product_name":     info.get("product_name", ""),
        "firmware_version": info.get("firmware_version", ""),
        "selected_sensors": [],
    }
    devices[device_id] = device
    _save_hw_devices(devices)
    log.info("HomeWizard device added  id=%s  ip=%s  type=%s  api_v=%s",
             device_id, ip, device["product_type"], api_v)
    return jsonify(device), 201


@app.route("/api/homewizard/devices/<device_id>", methods=["PATCH"])
def hw_update_device(device_id):
    """Update editable device fields: name, appliance_icon."""
    devices = _hw_devices()
    if device_id not in devices:
        return jsonify({"error": "Niet gevonden"}), 404
    body = request.get_json(force=True) or {}
    for key in ("name", "appliance_icon"):
        if key in body:
            devices[device_id][key] = body[key]
    _save_hw_devices(devices)
    return jsonify(devices[device_id])


@app.route("/api/homewizard/devices/<device_id>", methods=["DELETE"])
def hw_delete_device(device_id):
    devices = _hw_devices()
    if device_id not in devices:
        return jsonify({"error": "Niet gevonden"}), 404
    del devices[device_id]
    _save_hw_devices(devices)
    return jsonify({"ok": True})


def _hw_data_path(dev: dict) -> str:
    return "/api/v2/measurement" if dev.get("api_version") == 2 else "/api/v1/data"


@app.route("/api/homewizard/devices/<device_id>/discover")
def hw_discover(device_id):
    """Fetch live data and return annotated sensor list for selection UI."""
    devices = _hw_devices()
    if device_id not in devices:
        return jsonify({"error": "Niet gevonden"}), 404
    device = devices[device_id]
    try:
        data = _hw_fetch(device["ip"], _hw_data_path(device), token=device.get("token"))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    selected = set(device.get("selected_sensors") or [])
    sensors  = []
    for key, value in data.items():
        if not isinstance(value, (int, float)):
            continue
        meta = _hw_sensor_meta(key)
        sensors.append({
            "key":      key,
            "label":    meta["label"],
            "unit":     meta["unit"],
            "group":    meta["group"],
            "value":    value,
            "selected": key in selected,
        })

    sensors.sort(key=lambda s: (s["group"], s["label"]))
    return jsonify({"device": device, "sensors": sensors})


@app.route("/api/homewizard/devices/<device_id>/sensors", methods=["PUT"])
def hw_save_sensors(device_id):
    devices = _hw_devices()
    if device_id not in devices:
        return jsonify({"error": "Niet gevonden"}), 404
    body = request.get_json(force=True)
    devices[device_id]["selected_sensors"] = body.get("sensors", [])
    _save_hw_devices(devices)
    log.info("HomeWizard sensors updated  id=%s  count=%d",
             device_id, len(devices[device_id]["selected_sensors"]))
    return jsonify({"ok": True})


@app.route("/api/homewizard/probe")
def hw_probe_endpoint():
    """Diagnose endpoint: raw probe of a HomeWizard device IP.
    Returns what /api and /api/v1/data respond with (status, content-type, body snippet).
    Usage: GET /api/homewizard/probe?ip=10.10.20.230
    """
    ip = (request.args.get("ip") or "").strip()
    if not ip:
        return jsonify({"error": "ip parameter vereist"}), 400
    result = {}
    for path in ("/api", "/api/v1/data", "/api/v1/state"):
        for scheme in ("http", "https"):
            key = f"{scheme}:{path}"
            try:
                kw = {"timeout": 3}
                if scheme == "https":
                    kw["verify"] = False
                resp = _req.get(f"{scheme}://{ip}{path}", **kw)
                ct   = resp.headers.get("Content-Type", "")
                body = resp.text[:300]
                result[key] = {
                    "status": resp.status_code,
                    "content_type": ct,
                    "body_snippet": body,
                    "is_json": "json" in ct or body.lstrip().startswith("{"),
                    "is_html": "html" in ct or body.lstrip().startswith("<!"),
                }
            except Exception as exc:
                result[key] = {"error": str(exc)}
    return jsonify(result)


@app.route("/api/homewizard/devices/<device_id>/pair", methods=["POST"])
def hw_pair_v2(device_id):
    """Obtain a v2 token by triggering the button-press flow.
    The user must press the physical button on the device within 30 s of this call.
    """
    devices = _hw_devices()
    if device_id not in devices:
        return jsonify({"error": "Niet gevonden"}), 404
    dev = devices[device_id]
    ip  = dev["ip"]
    try:
        resp = _req.post(
            f"https://{ip}/api/user",
            json={"name": "local/marstek-dashboard"},
            headers={"X-Api-Version": "2"},
            timeout=35,
            verify=False,
        )
        resp.raise_for_status()
        token = resp.json().get("token", "")
        if not token:
            raise ValueError("Geen token ontvangen — is de knop ingedrukt?")
        dev["token"]       = token
        dev["api_version"] = 2
        _save_hw_devices(devices)
        log.info("HomeWizard v2 paired  id=%s  ip=%s", device_id, ip)
        return jsonify({"ok": True, "token_hint": f"…{token[-4:]}"})
    except Exception as exc:
        log.warning("HomeWizard v2 pair failed  id=%s  err=%s", device_id, exc)
        return jsonify({"error": str(exc)}), 502


@app.route("/api/homewizard/data")
def hw_data():
    """Poll all HomeWizard devices and return selected sensor values."""
    devices = _hw_devices()
    result  = []
    for dev in devices.values():
        selected = dev.get("selected_sensors") or []
        entry = {
            "id":              dev["id"],
            "name":            dev["name"],
            "product_type":    dev.get("product_type", ""),
            "api_version":     dev.get("api_version", 1),
            "appliance_icon":  dev.get("appliance_icon", ""),
            "reachable":       False,
            "sensors":         {},
            "error":           None,
        }
        try:
            data = _hw_fetch(dev["ip"], _hw_data_path(dev), token=dev.get("token"))
            entry["reachable"] = True
            for key in selected:
                if key in data and isinstance(data[key], (int, float)):
                    meta = _hw_sensor_meta(key)
                    entry["sensors"][key] = {
                        "value": data[key],
                        "label": meta["label"],
                        "unit":  meta["unit"],
                        "group": meta["group"],
                        "power": meta.get("power", False),
                    }
        except Exception as exc:
            entry["error"] = str(exc)
            log.warning("HomeWizard poll failed  id=%s  err=%s", dev["id"], exc)
        result.append(entry)

    return jsonify({"devices": result, "ts": int(time.time())})


@app.route("/api/homewizard/localsubnet")
def hw_local_subnet():
    return jsonify({"subnet": _local_subnet()})


@app.route("/api/homewizard/scan")
def hw_scan():
    """Scan a subnet for HomeWizard devices (concurrent probing)."""
    subnet_str = (request.args.get("subnet") or _local_subnet()).strip()
    try:
        network = ipaddress.IPv4Network(subnet_str, strict=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if network.num_addresses > 1024:
        return jsonify({"error": "Subnet te groot (max /22)"}), 400

    hosts = list(network.hosts())
    log.info("HomeWizard scan  subnet=%s  hosts=%d", subnet_str, len(hosts))

    found = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        futures = {pool.submit(_hw_probe, str(h)): str(h) for h in hosts}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result:
                    found.append(result)
            except Exception as exc:
                log.debug("Probe exception: %s", exc)

    found.sort(key=lambda d: [int(x) for x in d["ip"].split(".")])
    log.info("HomeWizard scan complete  found=%d", len(found))
    return jsonify({"subnet": subnet_str, "found": found})


# ---------------------------------------------------------------------------
# Flow source mappings
# ---------------------------------------------------------------------------

FLOW_SOURCES_FILE = os.path.join(BASE_DIR, "flow_sources.json")

FLOW_SLOT_DEFS = {
    "net_power":  {"label": "Net vermogen",      "unit": "W",
                   "desc": "Positief = import van net, negatief = export. HomeWizard P1 power_w past direct."},
    "bat_power":  {"label": "Batterijvermogen",   "unit": "W",
                   "desc": "Positief = ontladen, negatief = laden. Standaard: som van alle ESPHome batterijen."},
    "voltage_l1": {"label": "Spanning L1",         "unit": "V", "desc": "Fasespanning L1 (V)"},
    "voltage_l2": {"label": "Spanning L2",         "unit": "V", "desc": "Fasespanning L2 (V)"},
    "voltage_l3": {"label": "Spanning L3",         "unit": "V", "desc": "Fasespanning L3 (V)"},
}

_flow_live_cache: dict = {"data": {}, "ts": 0}


def _flow_sources_cfg() -> dict:
    if os.path.exists(FLOW_SOURCES_FILE):
        with open(FLOW_SOURCES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


@app.route("/api/flow/sources", methods=["GET"])
def get_flow_sources():
    return jsonify(_flow_sources_cfg())


@app.route("/api/flow/sources", methods=["PUT"])
def put_flow_sources():
    body = request.get_json(force=True)
    # Validate: only allow known slots
    cleaned = {k: v for k, v in body.items() if k in FLOW_SLOT_DEFS}
    with open(FLOW_SOURCES_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)
    _flow_live_cache["ts"] = 0  # invalidate cache
    log.info("Flow sources updated: %s", list(cleaned.keys()))
    return jsonify({"ok": True})


@app.route("/api/flow/options")
def get_flow_options():
    """Return all available HomeWizard sensor options, grouped by unit, with current live values."""
    options = []
    for dev in _hw_devices().values():
        try:
            data = _hw_fetch(dev["ip"], _hw_data_path(dev), token=dev.get("token"), timeout=3)
        except Exception:
            continue
        for key, value in data.items():
            if not isinstance(value, (int, float)):
                continue
            meta = _hw_sensor_meta(key)
            options.append({
                "source":      "homewizard",
                "device_id":   dev["id"],
                "device_name": dev["name"],
                "sensor":      key,
                "label":       f"{dev['name']} — {meta['label']}",
                "unit":        meta["unit"],
                "value":       value,
            })
    return jsonify({"slots": FLOW_SLOT_DEFS, "options": options})


@app.route("/api/flow/live")
def get_flow_live():
    """Resolve current values for all configured flow source slots."""
    now = time.time()
    if now - _flow_live_cache["ts"] < 5:   # 5 s TTL
        return jsonify(_flow_live_cache["data"])

    cfg     = _flow_sources_cfg()
    devices = _hw_devices()
    result  = {}

    for slot, slot_cfg in cfg.items():
        if not slot_cfg or slot_cfg.get("source") != "homewizard":
            continue
        dev_id = slot_cfg.get("device_id")
        sensor = slot_cfg.get("sensor")
        if not dev_id or not sensor or dev_id not in devices:
            continue
        dev = devices[dev_id]
        try:
            data  = _hw_fetch(dev["ip"], _hw_data_path(dev), token=dev.get("token"), timeout=3)
            value = data.get(sensor)
            if value is None or not isinstance(value, (int, float)):
                continue
            if slot_cfg.get("invert"):
                value = -value
            result[slot] = {
                "value":        value,
                "source_label": f"{dev['name']} / {sensor}",
            }
        except Exception as exc:
            result[slot] = {"error": str(exc)}

    _flow_live_cache["data"] = result
    _flow_live_cache["ts"]   = now
    return jsonify(result)


# ---------------------------------------------------------------------------
# ENTSO-E Transparency Platform – quarter-hour prices
# ---------------------------------------------------------------------------

ENTSOE_SETTINGS_FILE = os.path.join(BASE_DIR, "entsoe_settings.json")
ENTSOE_API_URL = "https://web-api.tp.entsoe.eu/api"
ENTSOE_ZONES = {
    "BE": "10YBE----------2",
    "NL": "10YNL----------L",
}
_entsoe_cache: dict = {}


def _entsoe_settings() -> dict:
    if os.path.exists(ENTSOE_SETTINGS_FILE):
        with open(ENTSOE_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _parse_entsoe_xml(xml_text: str) -> list:
    """Parse ENTSO-E Publication_MarketDocument XML → list of price dicts (UTC times)."""
    root = ET.fromstring(xml_text)
    ns_m = _re.match(r"\{(.+)\}", root.tag)
    ns = f"{{{ns_m.group(1)}}}" if ns_m else ""

    # Surface API-level errors embedded in XML
    reason = root.find(f".//{ns}Reason/{ns}Text") or root.find(f".//{ns}Text")
    code_el = root.find(f".//{ns}Reason/{ns}code") or root.find(f".//{ns}code")
    if code_el is not None and code_el.text and code_el.text.strip() != "999":
        raise ValueError(f"ENTSO-E fout: {reason.text if reason is not None else 'onbekend'}")

    rows = []
    for ts in root.findall(f"{ns}TimeSeries"):
        for period in ts.findall(f"{ns}Period"):
            res_el = period.find(f"{ns}resolution")
            resolution = (res_el.text or "PT60M").strip()
            interval_min = {"PT15M": 15, "PT30M": 30, "PT60M": 60}.get(resolution, 60)
            interval = timedelta(minutes=interval_min)

            ti = period.find(f"{ns}timeInterval")
            if ti is None:
                continue
            start_el = ti.find(f"{ns}start")
            if start_el is None:
                continue
            period_start = datetime.fromisoformat(start_el.text.replace("Z", "+00:00"))

            for point in period.findall(f"{ns}Point"):
                pos_el   = point.find(f"{ns}position")
                price_el = point.find(f"{ns}price.amount")
                if pos_el is None or price_el is None:
                    continue
                pos        = int(pos_el.text)
                price_kwh  = float(price_el.text) / 1000.0
                slot_start = period_start + (pos - 1) * interval
                slot_end   = slot_start + interval
                rows.append({
                    "from":                slot_start.isoformat(),
                    "till":                slot_end.isoformat(),
                    "marketPrice":         price_kwh,
                    "marketPriceTax":      0.0,
                    "sourcingMarkupPrice": 0.0,
                    "energyTaxPrice":      0.0,
                })

    rows.sort(key=lambda r: r["from"])
    return rows


def _fetch_entsoe_day(api_key: str, target_date: date, country: str = "BE",
                      tz_name: str | None = None) -> list:
    zone_id = ENTSOE_ZONES.get(country, ENTSOE_ZONES["BE"])
    if not tz_name:
        tz_name = _entsoe_settings().get("timezone") or "Europe/Brussels"
    tz = ZoneInfo(tz_name)

    local_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
    local_end   = local_start + timedelta(days=1)
    utc_start   = local_start.astimezone(ZoneInfo("UTC"))
    utc_end     = local_end.astimezone(ZoneInfo("UTC"))

    params = {
        "securityToken": api_key,
        "documentType":  "A44",
        "in_Domain":     zone_id,
        "out_Domain":    zone_id,
        "periodStart":   utc_start.strftime("%Y%m%d%H%M"),
        "periodEnd":     utc_end.strftime("%Y%m%d%H%M"),
    }
    log.info("ENTSO-E fetch  country=%s  date=%s  start=%s  end=%s",
             country, target_date, params["periodStart"], params["periodEnd"])
    resp = _req.get(ENTSOE_API_URL, params=params, timeout=15)
    resp.raise_for_status()

    all_rows = _parse_entsoe_xml(resp.text)
    log.debug("ENTSO-E raw rows: %d", len(all_rows))

    # Convert UTC → local and filter to exactly the requested local date
    result = []
    for row in all_rows:
        slot_local = datetime.fromisoformat(row["from"]).astimezone(tz)
        till_local = datetime.fromisoformat(row["till"]).astimezone(tz)
        if slot_local.date() == target_date:
            result.append({**row, "from": slot_local.isoformat(), "till": till_local.isoformat()})

    log.info("ENTSO-E result  country=%s  date=%s  rows=%d", country, target_date, len(result))
    return result


@app.route("/api/entsoe/settings", methods=["GET"])
def get_entsoe_settings_route():
    s   = _entsoe_settings()
    key = s.get("apiKey", "")
    return jsonify({
        "configured":  bool(key),
        "apiKeyHint":  f"…{key[-4:]}" if len(key) > 4 else ("✓" if key else ""),
        "timezone":    s.get("timezone") or "Europe/Brussels",
        "country":     s.get("country")  or "BE",
    })


@app.route("/api/entsoe/settings", methods=["POST"])
def set_entsoe_settings_route():
    body     = request.get_json(force=True)
    s        = _entsoe_settings()
    key      = (body.get("apiKey")   or "").strip()
    timezone = (body.get("timezone") or s.get("timezone") or "Europe/Brussels").strip()
    country  = (body.get("country")  or s.get("country")  or "BE").strip().upper()

    # Validate timezone
    try:
        ZoneInfo(timezone)
    except Exception:
        return jsonify({"error": f"Onbekende tijdzone: {timezone}"}), 400

    new_settings = {**s, "timezone": timezone, "country": country}
    if "apiKey" in body:  # explicitly provided (even empty = clear)
        new_settings["apiKey"] = key

    with open(ENTSOE_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(new_settings, f, indent=2)
    _entsoe_cache.clear()
    log.info("ENTSO-E settings updated  timezone=%s  country=%s  key_set=%s",
             timezone, country, bool(new_settings.get("apiKey")))
    return jsonify({"ok": True})


@app.route("/api/prices/entsoe")
def get_entsoe_prices():
    s       = _entsoe_settings()
    api_key = s.get("apiKey", "").strip()
    if not api_key:
        return jsonify({"error": "ENTSO-E API sleutel niet geconfigureerd. Voeg toe via Instellingen."}), 400

    # Use configured country/timezone; request arg can override country
    country  = (request.args.get("country") or s.get("country") or "BE").upper()
    tz_name  = s.get("timezone") or "Europe/Brussels"
    today    = date.today()
    tomorrow = today + timedelta(days=1)

    cache_key = f"entsoe_{country}_{tz_name}_{today.isoformat()}"
    cached = _entsoe_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < 1800:
        return jsonify(cached["data"])

    try:
        today_prices    = _fetch_entsoe_day(api_key, today, country, tz_name)
        tomorrow_prices: list = []
        try:
            tomorrow_prices = _fetch_entsoe_day(api_key, tomorrow, country, tz_name)
        except Exception as exc2:
            log.warning("ENTSO-E tomorrow prices unavailable: %s", exc2)

        result = {
            "today":    today_prices,
            "tomorrow": tomorrow_prices,
            "source":   "entsoe",
            "loggedIn": False,
            "email":    None,
        }
        _entsoe_cache[cache_key] = {"data": result, "ts": time.time()}
        return jsonify(result)
    except Exception as exc:
        log.error("ENTSO-E prices error: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 502


# ---------------------------------------------------------------------------
# Home Assistant integration
# ---------------------------------------------------------------------------

HA_SETTINGS_FILE = os.path.join(BASE_DIR, "ha_settings.json")
_ha_sensor_cache: dict = {"data": {}, "ts": 0}


def _ha_settings() -> dict:
    if os.path.exists(HA_SETTINGS_FILE):
        with open(HA_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _ha_effective_settings() -> dict:
    """
    Effective HA connection for backend API calls.
    Inside a HA add-on, always use http://supervisor/core + SUPERVISOR_TOKEN
    because that is the only reliably authenticated path.
    Falls back to the user-configured settings when no supervisor token exists.
    """
    sup_token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if sup_token:
        return {"url": "http://supervisor/core", "token": sup_token}
    return _ha_settings()


def _ha_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ha_call_service(domain: str, service: str, data: dict) -> bool:
    """Call a HA service (e.g. number.set_value) via the effective HA connection."""
    s = _ha_effective_settings()
    if not s.get("url") or not s.get("token"):
        log.debug("_ha_call_service: HA not configured")
        return False
    try:
        r = _req.post(
            f"{s['url']}/api/services/{domain}/{service}",
            headers=_ha_headers(s["token"]),
            json=data,
            timeout=5,
            verify=False,
        )
        if r.status_code in (200, 201):
            log.info("HA service %s/%s OK  data=%s", domain, service, data)
            return True
        log.warning("HA service %s/%s → HTTP %d: %s", domain, service, r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.warning("HA service %s/%s failed: %s", domain, service, exc)
        return False


@app.route("/api/ha/settings", methods=["GET"])
def get_ha_settings():
    s = _ha_settings()
    return jsonify({
        "configured": bool(s.get("token")),
        "url":        s.get("url", ""),
        "tokenHint":  f"…{s['token'][-4:]}" if s.get("token") else "",
    })


@app.route("/api/ha/settings", methods=["POST"])
def post_ha_settings():
    body = request.get_json(force=True)
    current = _ha_settings()

    url = (body.get("url") or "").strip().rstrip("/")
    token = body.get("token", "").strip()

    # URL is optional when running as a HA add-on (supervisor handles connection)
    s = {"url": url, "token": token if token else current.get("token", "")}
    with open(HA_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    _ha_sensor_cache["ts"] = 0
    return jsonify({"ok": True})


@app.route("/api/ha/test", methods=["POST"])
def test_ha():
    """Test HA connection and return server info."""
    s = _ha_effective_settings()
    if not s.get("token") or not s.get("url"):
        return jsonify({"error": "Niet geconfigureerd."}), 400
    try:
        r = _req.get(f"{s['url']}/api/", headers=_ha_headers(s["token"]), timeout=5, verify=False)
        if r.status_code == 401:
            return jsonify({"error": "Ongeldige token (401 Unauthorized)."}), 401
        r.raise_for_status()
        data = r.json()
        return jsonify({"ok": True, "message": data.get("message", "OK"), "version": data.get("version")})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/api/ha/entities")
def get_ha_entities():
    """Return all HA states (numeric entities only) for sensor selection."""
    s = _ha_effective_settings()
    if not s.get("token") or not s.get("url"):
        return jsonify({"entities": []})

    now = time.time()
    if now - _ha_sensor_cache["ts"] < 30 and _ha_sensor_cache["data"]:
        return jsonify({"entities": _ha_sensor_cache["data"]})

    try:
        r = _req.get(f"{s['url']}/api/states", headers=_ha_headers(s["token"]), timeout=8, verify=False)
        r.raise_for_status()
        states = r.json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    entities = []
    for state in states:
        entity_id = state.get("entity_id", "")
        raw_state = state.get("state", "")
        attrs = state.get("attributes", {})
        unit = attrs.get("unit_of_measurement", "")
        friendly = attrs.get("friendly_name", entity_id)

        # Only include numeric entities with useful units
        try:
            float(raw_state)
        except (ValueError, TypeError):
            continue

        entities.append({
            "entity_id":    entity_id,
            "friendly_name": friendly,
            "state":        raw_state,
            "unit":         unit,
            "domain":       entity_id.split(".")[0] if "." in entity_id else "",
        })

    # Sort: sensors first, then by name
    entities.sort(key=lambda e: (0 if e["domain"] == "sensor" else 1, e["friendly_name"].lower()))

    _ha_sensor_cache["data"] = entities
    _ha_sensor_cache["ts"]   = now
    return jsonify({"entities": entities})


@app.route("/api/ha/state/<path:entity_id>")
def get_ha_state(entity_id):
    """Fetch current state of a single HA entity."""
    s = _ha_effective_settings()
    if not s.get("token") or not s.get("url"):
        return jsonify({"error": "Niet geconfigureerd."}), 400
    try:
        r = _req.get(f"{s['url']}/api/states/{entity_id}",
                     headers=_ha_headers(s["token"]), timeout=5, verify=False)
        if r.status_code == 404:
            return jsonify({"error": f"Entity '{entity_id}' niet gevonden."}), 404
        r.raise_for_status()
        data = r.json()
        attrs = data.get("attributes", {})
        return jsonify({
            "entity_id":    entity_id,
            "state":        data.get("state"),
            "unit":         attrs.get("unit_of_measurement", ""),
            "friendly_name": attrs.get("friendly_name", entity_id),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/api/ha/poll", methods=["POST"])
def poll_ha_sensors():
    """Batch-poll a list of entity_ids. Returns {entity_id: {value, unit}} map."""
    s = _ha_effective_settings()
    if not s.get("token") or not s.get("url"):
        return jsonify({"error": "Niet geconfigureerd."}), 400

    body = request.get_json(force=True)
    entity_ids = body.get("entity_ids", [])
    if not entity_ids:
        return jsonify({})

    result = {}
    headers = _ha_headers(s["token"])

    def fetch_one(eid):
        try:
            r = _req.get(f"{s['url']}/api/states/{eid}", headers=headers, timeout=4, verify=False)
            if not r.ok:
                return eid, None
            data = r.json()
            attrs = data.get("attributes", {})
            try:
                value = float(data.get("state", ""))
            except (ValueError, TypeError):
                value = None
            return eid, {"value": value, "unit": attrs.get("unit_of_measurement", "")}
        except Exception:
            return eid, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for eid, val in pool.map(fetch_one, entity_ids):
            if val is not None:
                result[eid] = val

    return jsonify(result)


# ---------------------------------------------------------------------------
# Forecast.Solar
# ---------------------------------------------------------------------------

FORECAST_SETTINGS_FILE = os.path.join(BASE_DIR, "forecast_settings.json")
_forecast_cache: dict = {"data": None, "ts": 0}
FORECAST_CACHE_FILE   = os.path.join(DATA_DIR, "forecast_cache.json")
_FORECAST_TTL_DEFAULT = 900    # 15 min default (Personal Pro = 1 req/15min)


def _forecast_cache_ttl() -> int:
    """Return configured update interval in seconds, falling back to 15 min."""
    try:
        return int(_forecast_settings().get("update_interval") or _FORECAST_TTL_DEFAULT)
    except Exception:
        return _FORECAST_TTL_DEFAULT


def _load_forecast_disk_cache() -> None:
    """Restore in-memory forecast cache from disk on startup."""
    try:
        with open(FORECAST_CACHE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if time.time() - saved.get("ts", 0) < _forecast_cache_ttl():
            _forecast_cache["data"] = saved["data"]
            _forecast_cache["ts"]   = saved["ts"]
            log.info("forecast: restored from disk cache (age=%.0fs)",
                     time.time() - saved["ts"])
    except Exception:
        pass


def _save_forecast_disk_cache() -> None:
    try:
        with open(FORECAST_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"data": _forecast_cache["data"], "ts": _forecast_cache["ts"]}, f)
    except Exception as exc:
        log.debug("forecast: disk cache write failed: %s", exc)

def _forecast_settings() -> dict:
    if os.path.exists(FORECAST_SETTINGS_FILE):
        with open(FORECAST_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

@app.route("/api/forecast/settings", methods=["GET"])
def get_forecast_settings():
    s = _forecast_settings()
    return jsonify({
        "configured": bool(s.get("api_key")),
        "apiKeyHint": f"…{s['api_key'][-4:]}" if s.get("api_key") else "",
        "lat":    s.get("lat", ""),
        "lon":    s.get("lon", ""),
        "strings": s.get("strings", []),
    })

@app.route("/api/forecast/settings", methods=["POST"])
def post_forecast_settings():
    body = request.get_json(force=True) or {}
    current = _forecast_settings()
    if body.get("api_key"):
        current["api_key"] = body["api_key"].strip()
    if "lat" in body:    current["lat"]     = body["lat"]
    if "lon" in body:    current["lon"]     = body["lon"]
    if "strings" in body: current["strings"] = body["strings"]
    with open(FORECAST_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f)
    _forecast_cache["data"] = None  # invalidate
    return jsonify({"ok": True})

def _fetch_forecast(s: dict) -> dict:
    """Fetch from forecast.solar and return merged watts dict for all strings."""
    api_key = s.get("api_key", "")
    lat  = s.get("lat", "")
    lon  = s.get("lon", "")
    strings = s.get("strings", [])
    if not strings:
        strings = [{"kwp": s.get("kwp", 1), "az": s.get("az", 0), "dec": s.get("dec", 35)}]

    merged_watts = {}
    merged_wh_period = {}
    merged_wh_day = {}
    errors = []

    for st in strings:
        kwp = st.get("kwp", 1)
        az  = st.get("az", 0)
        dec = st.get("dec", 35)
        if api_key:
            url = f"https://api.forecast.solar/{api_key}/estimate/{lat}/{lon}/{dec}/{az}/{kwp}"
        else:
            url = f"https://api.forecast.solar/estimate/{lat}/{lon}/{dec}/{az}/{kwp}"
        try:
            resp = _req.get(url, timeout=15)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("X-Ratelimit-Period", 3600))
                raise Exception(f"429 Too Many Requests – probeer opnieuw over {retry_after}s "
                                f"(forecast.solar limiet bereikt)")
            resp.raise_for_status()
            data = resp.json()
            w  = data.get("result", {}).get("watts", {})
            wp = data.get("result", {}).get("watt_hours_period", {})
            wd = data.get("result", {}).get("watt_hours_day", {})
            for k, v in w.items():
                merged_watts[k] = merged_watts.get(k, 0) + v
            for k, v in wp.items():
                merged_wh_period[k] = merged_wh_period.get(k, 0) + v
            for k, v in wd.items():
                merged_wh_day[k] = merged_wh_day.get(k, 0) + v
        except Exception as e:
            errors.append(str(e))

    return {
        "watts": merged_watts,
        "watt_hours_period": merged_wh_period,
        "watt_hours_day": merged_wh_day,
        "errors": errors,
    }

@app.route("/api/forecast/estimate")
def get_forecast_estimate():
    s = _forecast_settings()
    if not s.get("lat") or not s.get("lon"):
        return jsonify({"error": "Locatie niet ingesteld."}), 400

    now = time.time()
    if _forecast_cache["data"] and (now - _forecast_cache["ts"]) < _forecast_cache_ttl():
        return jsonify(_forecast_cache["data"])

    result = _fetch_forecast(s)
    # If all strings failed (e.g. 429), keep serving the stale cache
    # so the UI doesn't lose its forecast data and we don't hammer the API.
    if result["errors"] and not result["watts"] and _forecast_cache["data"]:
        log.warning("forecast: fetch failed (%s) – serving stale cache", result["errors"])
        stale = dict(_forecast_cache["data"])
        stale["stale"] = True
        stale["fetch_error"] = result["errors"][0]
        return jsonify(stale)
    _forecast_cache["data"] = result
    _forecast_cache["ts"]   = now
    _save_forecast_disk_cache()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Forecast actuals (werkelijke zonneopbrengst vs voorspelling)
# ---------------------------------------------------------------------------

FORECAST_ACTUAL_FILE = os.path.join(BASE_DIR, "forecast_actual_source.json")


def _forecast_actual_source() -> dict:
    try:
        with open(FORECAST_ACTUAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"source": "none", "entity_id": ""}


@app.route("/api/forecast/actual-source", methods=["GET"])
def get_forecast_actual_source():
    return jsonify(_forecast_actual_source())


@app.route("/api/forecast/actual-source", methods=["POST"])
def save_forecast_actual_source():
    body = request.get_json(force=True) or {}
    with open(FORECAST_ACTUAL_FILE, "w", encoding="utf-8") as f:
        json.dump({"source": body.get("source", "none"),
                   "entity_id": body.get("entity_id", "")}, f, indent=2)
    return jsonify({"ok": True})


@app.route("/api/forecast/actuals")
def get_forecast_actuals():
    """Return 15-min actual solar watts for a given date from InfluxDB or HA history."""
    from datetime import datetime as _dt, timedelta as _td
    import pytz as _pytz

    date_str = request.args.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    cfg  = _forecast_actual_source()
    src  = cfg.get("source", "none")
    result: dict[str, float] = {}
    tz_name  = (_forecast_settings().get("timezone") or
                _entsoe_settings().get("timezone") or "Europe/Brussels")

    if src == "influx":
        influx_src  = _load_influx_source()
        conn        = _load_influx_conn()
        mapping     = influx_src.get("mappings", {}).get("solar_w")
        if not mapping:
            return jsonify({"error": "InfluxDB zonnepanelen slot niet geconfigureerd."}), 400
        if isinstance(mapping, list):
            mapping = mapping[0]
        url      = influx_src.get("url") or conn.get("url", "")
        version  = influx_src.get("version") or conn.get("version", "v1")
        database = influx_src.get("database", "")
        username = conn.get("username", "")
        password = conn.get("password", "")
        token    = conn.get("token", "")
        org      = conn.get("org", "")
        field    = mapping.get("field", "value")
        meas     = mapping.get("measurement") or influx_src.get("measurement", "")
        tag_key  = mapping.get("tag_key", "")
        tag_val  = mapping.get("tag_value", "")
        if not url or not meas:
            return jsonify({"error": "InfluxDB niet volledig geconfigureerd."}), 400
        try:
            if version == "v1":
                # Extend UTC range by ±14h to cover any timezone, then filter
                # results to the target local date after converting to local time
                from datetime import datetime as _dt2, timedelta as _td2
                _d = _dt2.strptime(date_str, "%Y-%m-%d")
                _start = (_d - _td2(hours=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
                _end   = (_d + _td2(hours=38)).strftime("%Y-%m-%dT%H:%M:%SZ")
                where_parts = [f"time >= '{_start}' AND time < '{_end}'"]
                if tag_key and tag_val:
                    where_parts.append(f'"{tag_key}" = \'{tag_val}\'')
                # No tz() in query – InfluxDB v1 tz() support varies; always
                # receive UTC and convert to local time ourselves.
                q = (f'SELECT mean("{field}") AS val FROM "{meas}"'
                     f' WHERE {" AND ".join(where_parts)}'
                     f" GROUP BY time(15m) fill(null)")
                data = _influx_v1_query(url, username, password, q, db=database)
                _tz_obj = _pytz.timezone(tz_name)
                for res in data.get("results", []):
                    for series in res.get("series", []):
                        for row in series.get("values", []):
                            ts_raw, val = row[0], row[1]
                            if val is None:
                                continue
                            # Convert UTC → local time
                            try:
                                dt_utc = _dt2.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                                    tzinfo=_pytz.utc)
                                ts = dt_utc.astimezone(_tz_obj).strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                ts = ts_raw[:19].replace("T", " ")
                            if ts.startswith(date_str):
                                result[ts] = float(val)
        except Exception as exc:
            log.warning("forecast/actuals influx error: %s", exc)
            return jsonify({"watts": {}, "warning": f"InfluxDB niet bereikbaar: {exc}"})

    elif src == "ha":
        entity_id = cfg.get("entity_id", "").strip()
        if not entity_id:
            return jsonify({"error": "Geen HA entiteit geconfigureerd."}), 400
        s = _ha_effective_settings()
        if not s.get("url") or not s.get("token"):
            return jsonify({"error": "HA niet geconfigureerd."}), 400
        try:
            url_ha = f"{s['url']}/api/history/period/{date_str}T00:00:00"
            r = _req.get(url_ha,
                         headers=_ha_headers(s["token"]),
                         params={"end_time": f"{date_str}T23:59:59",
                                 "filter_entity_id": entity_id,
                                 "minimal_response": "true"},
                         timeout=15, verify=False)
            r.raise_for_status()
            history = r.json()
            if not history or not history[0]:
                return jsonify({"watts": {}})
            states = history[0]
            # Bin into 15-min slots (HA returns UTC timestamps → convert to local)
            from datetime import datetime as _dt_ha
            _tz_ha = _pytz.timezone(tz_name)
            buckets: dict[str, list[float]] = {}
            for state in states:
                try:
                    val = float(state["state"])
                except (ValueError, TypeError, KeyError):
                    continue
                ts_raw = state.get("last_changed", "")
                try:
                    dt_utc = _dt_ha.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                        tzinfo=_pytz.utc)
                    dt_local = dt_utc.astimezone(_tz_ha)
                    local_date = dt_local.strftime("%Y-%m-%d")
                    if local_date != date_str:
                        continue
                    h, m = dt_local.hour, dt_local.minute
                except Exception:
                    local_str = ts_raw[:19].replace("T", " ")
                    if not local_str.startswith(date_str):
                        continue
                    h, m = int(local_str[11:13]), int(local_str[14:16])
                    local_date = date_str
                slot_m = (m // 15) * 15
                slot = f"{date_str} {h:02d}:{slot_m:02d}:00"
                buckets.setdefault(slot, []).append(val)
            for slot, vals in buckets.items():
                result[slot] = sum(vals) / len(vals)
        except Exception as exc:
            log.warning("forecast/actuals ha error: %s", exc)
            return jsonify({"error": f"HA history: {exc}"}), 502

    elif src == "flow":
        # Use solar_power entries from flow_cfg (same sources as the live dashboard).
        # Queries HA history for HA-sourced sensors; ESPHome sensors are read from
        # InfluxDB (written by the background writer) when available, else HA history
        # using the sensor name derived from the ESPHome entity.
        ha_s     = _ha_effective_settings()
        import pytz as _pytz2
        _tz2     = _pytz2.timezone(tz_name)

        # Load flow_cfg to find solar_power entities
        flow_cfg2: dict = {}
        try:
            with open(FLOW_CFG_SERVER_FILE, "r", encoding="utf-8") as _ff:
                raw2 = json.load(_ff)
            for k, v in raw2.items():
                flow_cfg2[k] = v if isinstance(v, list) else [v]
        except Exception:
            pass

        sol_entries = [
            (e["sensor"], bool(e.get("invert", False)), float(e.get("scale", 1) or 1))
            for e in flow_cfg2.get("solar_power", [])
            if e.get("source") == "homeassistant" and e.get("sensor")
        ]

        # Also pick up ESPHome solar sensors via InfluxDB if mapped
        influx_src2 = _load_influx_source()
        sol_mapping = influx_src2.get("mappings", {}).get("solar_w")
        use_influx_flow = bool(sol_mapping and influx_src2.get("database"))

        if use_influx_flow:
            # Delegate to influx path by temporarily acting as src=="influx"
            conn2    = _load_influx_conn()
            mapping2 = sol_mapping if not isinstance(sol_mapping, list) else sol_mapping[0]
            url2     = influx_src2.get("url") or conn2.get("url", "")
            version2 = influx_src2.get("version") or conn2.get("version", "v1")
            db2      = influx_src2.get("database", "")
            user2    = conn2.get("username", "")
            pass2    = conn2.get("password", "")
            field2   = mapping2.get("field", "value")
            meas2    = mapping2.get("measurement") or influx_src2.get("measurement", "")
            tkey2    = mapping2.get("tag_key", "")
            tval2    = mapping2.get("tag_value", "")
            if url2 and meas2:
                try:
                    from datetime import datetime as _dt3, timedelta as _td3
                    _d3 = _dt3.strptime(date_str, "%Y-%m-%d")
                    _s3 = (_d3 - _td3(hours=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    _e3 = (_d3 + _td3(hours=38)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    where3 = [f"time >= '{_s3}' AND time < '{_e3}'"]
                    if tkey2 and tval2:
                        where3.append(f'"{tkey2}" = \'{tval2}\'')
                    q3 = (f'SELECT mean("{field2}") AS val FROM "{meas2}"'
                          f' WHERE {" AND ".join(where3)}'
                          f" GROUP BY time(15m) fill(null)")
                    data3 = _influx_v1_query(url2, user2, pass2, q3, db=db2)
                    for res3 in data3.get("results", []):
                        for ser3 in res3.get("series", []):
                            for row3 in ser3.get("values", []):
                                ts3, val3 = row3[0], row3[1]
                                if val3 is None:
                                    continue
                                try:
                                    from datetime import datetime as _dti
                                    import pytz as _pyi
                                    dt_u = _dti.strptime(ts3[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_pyi.utc)
                                    ts_loc = dt_u.astimezone(_tz2).strftime("%Y-%m-%d %H:%M:%S")
                                except Exception:
                                    ts_loc = ts3[:19].replace("T", " ")
                                if ts_loc.startswith(date_str):
                                    result[ts_loc] = float(val3)
                except Exception as exc:
                    log.warning("forecast/actuals flow→influx error: %s", exc)

        # Diagnose empty result: warn when flow_cfg has no usable historical source
        if not result and not sol_entries and not use_influx_flow:
            all_solar = flow_cfg2.get("solar_power", [])
            if not all_solar:
                return jsonify({"watts": {}, "warning":
                    "Geen zonnepanelen bron ingesteld in Instellingen → Bronnen."})
            non_ha = [e.get("source", "?") for e in all_solar if e.get("source") != "homeassistant"]
            if non_ha:
                return jsonify({"watts": {}, "warning":
                    f"Historische data is alleen beschikbaar voor Home Assistant entiteiten. "
                    f"Huidige bron(nen): {', '.join(set(non_ha))}. "
                    f"Voeg een HA-sensor toe als zonnepanelen bron, of gebruik InfluxDB."})

        if not result and sol_entries and ha_s.get("url") and ha_s.get("token"):
            # Fall back to HA history for the solar HA entities
            base2 = ha_s["url"].rstrip("/")
            hdrs2 = _ha_headers(ha_s["token"])
            for eid2, inv2, sc2 in sol_entries:
                try:
                    r2 = _req.get(
                        f"{base2}/api/history/period/{date_str}T00:00:00",
                        headers=hdrs2,
                        params={"end_time": f"{date_str}T23:59:59",
                                "filter_entity_id": eid2,
                                "minimal_response": "true"},
                        timeout=15, verify=False)
                    if not r2.ok:
                        continue
                    hist2 = r2.json()
                    if not hist2 or not hist2[0]:
                        continue
                    from datetime import datetime as _dth
                    import pytz as _pyh
                    for st2 in hist2[0]:
                        try:
                            val2 = float(st2["state"]) * sc2 * (-1 if inv2 else 1)
                        except (ValueError, TypeError):
                            continue
                        traw2 = st2.get("last_changed", "")
                        try:
                            dtu2 = _dth.strptime(traw2[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_pyh.utc)
                            dtl2 = dtu2.astimezone(_tz2)
                            if dtl2.strftime("%Y-%m-%d") != date_str:
                                continue
                            h2, m2 = dtl2.hour, dtl2.minute
                        except Exception:
                            continue
                        slot2 = f"{date_str} {h2:02d}:{(m2 // 15)*15:02d}:00"
                        result[slot2] = result.get(slot2, 0.0) + val2
                except Exception as exc2:
                    log.warning("forecast/actuals flow→ha %s error: %s", eid2, exc2)

    return jsonify({"watts": result, "source": src})


# ---------------------------------------------------------------------------
# Debug endpoint
# ---------------------------------------------------------------------------

@app.route("/api/debug", methods=["GET"])
def debug_info():
    """Returns diagnostics: devices, Frank session state, log tail."""
    devices = load_devices()
    session = _frank_session()

    # Last 50 lines of log file
    log_lines = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            log_lines = f.readlines()[-50:]
    except Exception:
        log_lines = ["(log file not readable)"]

    # Test connectivity to each device (quick HEAD request)
    device_status = {}
    for dev in devices.values():
        try:
            r = _req.get(f"http://{dev['ip']}:{dev['port']}/", timeout=2)
            device_status[dev["id"]] = {"reachable": True, "http_status": r.status_code}
        except Exception as exc:
            device_status[dev["id"]] = {"reachable": False, "error": str(exc)}

    return jsonify({
        "server_time": datetime.now().isoformat(),
        "devices": list(devices.values()),
        "device_reachability": device_status,
        "frank_logged_in": bool(session.get("authToken")),
        "frank_email": session.get("email"),
        "frank_endpoint": session.get("endpoint"),
        "price_cache_keys": list(_price_cache.keys()),
        "log_tail": [l.rstrip() for l in log_lines],
    })


# ---------------------------------------------------------------------------
# Strategy & InfluxDB
# ---------------------------------------------------------------------------

# flow_cfg.json is written by the frontend (localStorage mirror) –
# we also maintain a server-side copy that the influx writer can read.
FLOW_CFG_SERVER_FILE = os.path.join(BASE_DIR, "flow_cfg.json")


@app.route("/api/flow/cfg", methods=["GET"])
def get_flow_cfg():
    try:
        with open(FLOW_CFG_SERVER_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({})


@app.route("/api/flow/cfg", methods=["POST"])
def post_flow_cfg():
    """Frontend calls this whenever marstek_flow_cfg changes in localStorage."""
    body = request.get_json(force=True) or {}
    with open(FLOW_CFG_SERVER_FILE, "w", encoding="utf-8") as f:
        json.dump(body, f)
    return jsonify({"ok": True})


@app.route("/api/strategy/settings", methods=["GET"])
def get_strategy_settings():
    return jsonify(load_strategy_settings())


@app.route("/api/strategy/settings", methods=["POST", "PATCH"])
def post_strategy_settings():
    body = request.get_json(force=True) or {}
    return jsonify(save_strategy_settings(body))


def _query_ha_hourly_consumption(days: int = 21) -> list[dict]:
    """
    Fallback consumption profile from HA history when InfluxDB data is sparse.
    Integrates HA history for net_power + solar_power slots to derive house_wh per hour-of-day.
    Returns [{hour: int, avg_wh: float}] averaged over `days` days.
    Falls back to influx_source.json entity_ids if flow_cfg has no HA sensors.
    """
    ha_s = _ha_effective_settings()
    if not ha_s.get("token") or not ha_s.get("url"):
        log.debug("HA history fallback: no HA token/URL configured")
        return []

    flow_cfg: dict = {}
    try:
        with open(FLOW_CFG_SERVER_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            flow_cfg[k] = v if isinstance(v, list) else [v]
    except Exception as exc:
        log.debug("HA history fallback: could not read flow_cfg: %s", exc)

    def ha_entries_for_slot(slot_key):
        return [(e["sensor"], e.get("invert", False), 1.0)
                for e in flow_cfg.get(slot_key, [])
                if e.get("source") == "homeassistant" and e.get("sensor")]

    net_entities = ha_entries_for_slot("net_power")
    sol_entities = ha_entries_for_slot("solar_power")

    # Fallback: use influx_source.json entity_ids if flow_cfg has no HA entities
    if not net_entities and not sol_entities:
        log.debug("HA history fallback: flow_cfg has no HA entities, trying influx_source.json")
        src = _load_influx_source()
        mappings = src.get("mappings", {})

        def _src_entries(key):
            m = mappings.get(key)
            if not m:
                return []
            entries = m if isinstance(m, list) else [m]
            result = []
            for e in entries:
                tv = e.get("tag_value", "")
                if tv:
                    sensor_id = f"sensor.{tv}"
                    invert    = bool(e.get("invert", False))
                    scale     = float(e.get("scale", 1) or 1)
                    result.append((sensor_id, invert, scale))
            return result

        net_entities = _src_entries("net_w")
        sol_entities = _src_entries("solar_w")
        log.debug("HA history fallback: influx_source net=%s sol=%s",
                  [e[0] for e in net_entities], [e[0] for e in sol_entities])

    all_eids = list({eid for eid, _, _ in net_entities + sol_entities})

    if not all_eids:
        log.debug("HA history fallback: no entities found in flow_cfg or influx_source")
        return []

    headers  = _ha_headers(ha_s["token"])
    base_url = ha_s["url"].rstrip("/")
    # Fetch up to requested days; chunked fetching prevents timeout
    ha_days  = min(days, 21)
    start_dt = datetime.now(timezone.utc) - timedelta(days=ha_days)
    tz_name  = _entsoe_settings().get("timezone", "Europe/Brussels")
    tz_local = ZoneInfo(tz_name)

    log.debug("HA history fallback: querying %d entities for %d days from %s",
              len(all_eids), ha_days, start_dt.strftime("%Y-%m-%d"))

    # Integrate each entity's power (W) history → Wh per (date, hour) bucket
    # Fetch in weekly chunks to avoid HTTP timeout on long periods.
    entity_hourly: dict[str, dict] = {}

    for eid in all_eids:
        try:
            states = []
            chunk_start = start_dt
            while chunk_start < datetime.now(timezone.utc):
                chunk_end = min(chunk_start + timedelta(days=7), datetime.now(timezone.utc))
                r = _req.get(
                    f"{base_url}/api/history/period/{chunk_start.isoformat()}",
                    headers=headers,
                    params={"filter_entity_id": eid,
                            "end_time": chunk_end.isoformat(),
                            "minimal_response": "true",
                            "no_attributes": "true",
                            "significant_changes_only": "false"},
                    timeout=60,
                    verify=False,
                )
                if not r.ok:
                    log.warning("HA history %s chunk %s → HTTP %s", eid,
                                chunk_start.strftime("%Y-%m-%d"), r.status_code)
                    chunk_start = chunk_end
                    continue
                chunk_hist = r.json()
                if chunk_hist and chunk_hist[0]:
                    states.extend(chunk_hist[0])
                chunk_start = chunk_end

            # Find scale for this entity (default 1.0 — kW sensors have scale=1000)
            eid_scale = 1.0
            for _eid, _inv, _sc in net_entities + sol_entities:
                if _eid == eid:
                    eid_scale = _sc
                    break

            hourly: dict = {}   # (date_str, hour) → Wh
            prev_t = prev_p = None

            for record in states:
                try:
                    t = datetime.fromisoformat(record["last_changed"].replace("Z", "+00:00"))
                    p = float(record["state"]) * eid_scale
                except (KeyError, ValueError, TypeError):
                    prev_t = prev_p = None
                    continue

                if prev_t is not None and prev_p is not None:
                    delta_h = (t - prev_t).total_seconds() / 3600.0
                    wh = prev_p * delta_h
                    local_prev = prev_t.astimezone(tz_local)
                    key = (local_prev.date().isoformat(), local_prev.hour)
                    hourly[key] = hourly.get(key, 0.0) + wh

                prev_t, prev_p = t, p

            entity_hourly[eid] = hourly
            log.debug("HA history %s: %d hour-buckets", eid, len(hourly))
        except Exception as exc:
            log.warning("HA history query failed for %s: %s", eid, exc)

    if not entity_hourly:
        return []

    # Collect all (date_str, hour) keys present
    all_keys: set = set()
    for d in entity_hourly.values():
        all_keys.update(d.keys())

    by_wd_hour: dict[tuple, list] = {}  # (weekday 0=Mon, hour) → [Wh]

    for date_str, hour in all_keys:
        key = (date_str, hour)

        solar_wh_val = sum(
            (-entity_hourly[eid].get(key, 0.0) if inv else entity_hourly[eid].get(key, 0.0))
            for eid, inv, _sc in sol_entities
            if eid in entity_hourly
        )
        net_wh = sum(
            (-entity_hourly[eid].get(key, 0.0) if inv else entity_hourly[eid].get(key, 0.0))
            for eid, inv, _sc in net_entities
            if eid in entity_hourly
        )
        # house ≈ solar + grid_import (battery not tracked here)
        house_wh = solar_wh_val + max(net_wh, 0.0)
        if house_wh >= 0:
            from datetime import date as _date
            wd = _date.fromisoformat(date_str).weekday()  # 0=Mon, 6=Sun
            by_wd_hour.setdefault((wd, hour), []).append(house_wh)

    result = []
    for (wd, hour), vals in sorted(by_wd_hour.items()):
        result.append({"weekday": wd, "hour": hour, "avg_wh": round(sum(vals) / len(vals), 1)})

    log.info("HA consumption history: %d (weekday, hour) buckets from %d days  entities=%s",
             len(result), ha_days, all_eids)
    return result


@app.route("/api/claude/usage")
def get_claude_usage():
    """Aggregated Claude API usage: today / this week / this month / all-time."""
    try:
        import strategy_claude as _sc
        return jsonify(_sc.get_usage_stats())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/ha/consumption-debug")
def ha_consumption_debug():
    """
    Diagnostic endpoint: shows exactly which HA entities are queried for
    the consumption profile and how many data points / hour-buckets come back.
    Useful for troubleshooting sparse HA history.
    """
    ha_s = _ha_effective_settings()
    if not ha_s.get("token") or not ha_s.get("url"):
        return jsonify({"error": "Geen HA token/URL geconfigureerd"}), 400

    flow_cfg: dict = {}
    try:
        with open(FLOW_CFG_SERVER_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            flow_cfg[k] = v if isinstance(v, list) else [v]
    except Exception:
        pass

    def ha_entries(slot_key):
        return [(e["sensor"], e.get("invert", False))
                for e in flow_cfg.get(slot_key, [])
                if e.get("source") == "homeassistant" and e.get("sensor")]

    net_ha  = ha_entries("net_power")
    sol_ha  = ha_entries("solar_power")
    src     = _load_influx_source()
    mappings = src.get("mappings", {})

    def src_entries(key):
        m = mappings.get(key)
        if not m:
            return []
        entries = m if isinstance(m, list) else [m]
        return [(f"sensor.{e['tag_value']}", bool(e.get("invert")))
                for e in entries if e.get("tag_value")]

    net_fallback = src_entries("net_w") if not net_ha else []
    sol_fallback = src_entries("solar_w") if not sol_ha else []

    all_eids = list({eid for eid, _ in net_ha + sol_ha + net_fallback + sol_fallback})

    headers  = _ha_headers(ha_s["token"])
    base_url = ha_s["url"].rstrip("/")
    probe_start = datetime.now(timezone.utc) - timedelta(days=3)

    entity_info = []
    for eid in all_eids:
        try:
            r = _req.get(
                f"{base_url}/api/history/period/{probe_start.isoformat()}",
                headers=headers,
                params={"filter_entity_id": eid, "minimal_response": "true",
                        "no_attributes": "true"},
                timeout=30,
                verify=False,
            )
            if not r.ok:
                entity_info.append({"entity_id": eid, "error": f"HTTP {r.status_code}",
                                    "records": 0})
                continue
            hist = r.json()
            records = len(hist[0]) if hist and hist[0] else 0
            sample  = hist[0][-1]["state"] if records else None
            entity_info.append({"entity_id": eid, "records_3d": records,
                                 "last_value": sample, "ok": records > 0})
        except Exception as exc:
            entity_info.append({"entity_id": eid, "error": str(exc), "records": 0})

    return jsonify({
        "ha_url":        ha_s.get("url"),
        "flow_cfg_net":  [e[0] for e in net_ha],
        "flow_cfg_solar": [e[0] for e in sol_ha],
        "fallback_net":  [e[0] for e in net_fallback],
        "fallback_solar": [e[0] for e in sol_fallback],
        "entities_queried": all_eids,
        "entity_probe":  entity_info,
        "note": (
            "entities_queried toont welke sensoren gebruikt worden. "
            "records_3d = aantal datapunten in de laatste 3 dagen. "
            "0 records = sensor niet gevonden of geen data."
        ),
    })


@app.route("/api/strategy/plan")
def get_strategy_plan():
    """Return the charging plan. ?date=YYYY-MM-DD for historical single-day view."""
    s = load_strategy_settings()
    tz_name    = _entsoe_settings().get("timezone", "Europe/Brussels")
    today_str  = date.today().isoformat()

    date_param = request.args.get("date", "").strip()
    is_historical = bool(date_param) and date_param < today_str
    target_date   = date_param if date_param else today_str

    if not is_historical:
        force_refresh = request.args.get("refresh") == "1"
        if not force_refresh and _plan_cache.get("result"):
            if s.get("strategy_mode") == "claude":
                # Claude mode: serve disk-restored or in-memory cache with a
                # short page-load TTL (5 min). Full fingerprint check only
                # happens in the hourly automation tick, not on every page load.
                cached_at_str = _plan_cache.get("fetched_at")
                if cached_at_str:
                    age_s = (datetime.now(timezone.utc)
                             - datetime.fromisoformat(cached_at_str)).total_seconds()
                    if age_s < 300:
                        result = dict(_plan_cache["result"])
                        live = _live_soc()
                        if live is not None:
                            result["soc_now"] = live
                        return jsonify(result)
                # Older than 5 min but still valid: update SoC and serve
                # without re-calling Claude (fingerprint check will guard it).
                pass  # fall through to _compute_forward_plan (fingerprint-gated)
            else:
                # Rule-based: 5-minute TTL
                cached_at_str = _plan_cache.get("fetched_at")
                if cached_at_str:
                    age_s = (datetime.now(timezone.utc)
                             - datetime.fromisoformat(cached_at_str)).total_seconds()
                    if age_s < 300:
                        result = dict(_plan_cache["result"])
                        live = _live_soc()
                        if live is not None:
                            result["soc_now"] = live
                        return jsonify(result)
        return jsonify(_compute_forward_plan(force_claude=force_refresh))

    if is_historical:
        # ── Historical mode: use InfluxDB actuals ─────────────────────────
        prices = []
        es = _entsoe_settings()
        if es.get("apiKey"):
            try:
                target_d = date.fromisoformat(target_date)
                prices  += _fetch_entsoe_day(es["apiKey"], target_d,
                                             es.get("country", "BE"), es.get("timezone"))
            except Exception as exc:
                log.warning("Strategy historical: ENTSO-E fetch error: %s", exc)

        actuals = query_day_actuals(target_date, tz_name)   # {hour: {solar_w, house_w, bat_soc, ...}}
        influx_available = len(actuals) > 0

        # Build solar_wh from actuals (W mean × 1 h = Wh)
        tz_obj   = ZoneInfo(tz_name)
        tgt_d    = date.fromisoformat(target_date)
        solar_wh: dict = {}
        consumption_by_hour = []
        for hour, row in actuals.items():
            slot_dt  = datetime(tgt_d.year, tgt_d.month, tgt_d.day,
                                int(hour), 0, 0, tzinfo=tz_obj)
            slot_key = slot_dt.isoformat()
            if "solar_w" in row:
                solar_wh[slot_key] = row["solar_w"]       # W mean ≈ Wh for 1-hour window
            if "house_w" in row:
                consumption_by_hour.append({"hour": int(hour), "avg_wh": row["house_w"]})

        # SOC at start of day (hour 0 reading, or first available)
        soc_now = 50.0
        for h in range(24):
            if h in actuals and "bat_soc" in actuals[h]:
                soc_now = actuals[h]["bat_soc"]
                break

        start_dt = datetime(tgt_d.year, tgt_d.month, tgt_d.day, 0, 0, 0, tzinfo=tz_obj)
        plan_slots = build_plan(prices, solar_wh, consumption_by_hour, soc_now, s,
                                start_dt=start_dt, num_slots=24)

        # Serialise actuals keyed by hour string for JSON transport
        actuals_out = {str(h): v for h, v in actuals.items()}

        return jsonify({
            "date":              target_date,
            "is_historical":     True,
            "slots":             plan_slots,
            "actuals":           actuals_out,
            "influx_available":  influx_available,
            "prices_available":  len(prices) > 0,
            "solar_available":   bool(solar_wh),
            "soc_now":           soc_now,
            "consumption_by_hour": consumption_by_hour,
        })

    # (forward mode handled above via _compute_forward_plan())
    abort(400)


@app.route("/api/strategy/history")
def get_strategy_history():
    """Return averaged hourly consumption profile (last N days)."""
    days = int(request.args.get("days", 21))
    data = query_avg_hourly_consumption(days=days)
    return jsonify({"hours": data})


@app.route("/api/influx/recent")
def get_influx_recent():
    """Return last N hours of energy flow data from InfluxDB."""
    hours = int(request.args.get("hours", 24))
    data  = query_recent_points(hours=hours)
    return jsonify({"points": data})


@app.route("/api/influx/status")
def get_influx_status():
    """Check InfluxDB connectivity."""
    try:
        from influxdb_client import InfluxDBClient  # type: ignore
        from influx_writer import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        health = client.health()
        return jsonify({"ok": True, "status": health.status, "url": INFLUX_URL,
                        "bucket": INFLUX_BUCKET, "org": INFLUX_ORG})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# InfluxDB connection scanner  (v1 + v2)
# ---------------------------------------------------------------------------

INFLUX_CONN_FILE = os.path.join(BASE_DIR, "influx_connection.json")

def _load_influx_conn() -> dict:
    try:
        with open(INFLUX_CONN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.route("/api/influx/connection", methods=["GET"])
def get_influx_connection():
    conn = _load_influx_conn()
    # Never expose password in plaintext over API – mask it
    safe = dict(conn)
    if safe.get("password"):
        safe["password"] = "••••••••"
    if safe.get("token"):
        safe["token"] = f"…{conn['token'][-6:]}" if len(conn.get("token","")) > 6 else "••••••"
    return jsonify(safe)


@app.route("/api/influx/connection", methods=["POST"])
def save_influx_connection():
    body    = request.get_json(force=True) or {}
    current = _load_influx_conn()
    # Keep existing secret if caller sends masked placeholder
    if body.get("password", "").startswith("•"):
        body["password"] = current.get("password", "")
    if body.get("token", "").startswith("…") or body.get("token", "").startswith("•"):
        body["token"] = current.get("token", "")
    current.update({k: v for k, v in body.items() if v != ""})
    with open(INFLUX_CONN_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return jsonify({"ok": True})


def _influx_v1_query(url: str, username: str, password: str,
                     q: str, db: str = "") -> dict:
    """Execute an InfluxQL query against a v1 server and return parsed JSON."""
    import base64 as _b64
    params = {"q": q}
    if db:
        params["db"] = db
    headers = {}
    if username:
        # requests encodes Basic Auth credentials as latin-1, which fails for
        # non-ASCII characters.  Build the header manually using UTF-8 instead.
        token = _b64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    r = _req.get(
        f"{url.rstrip('/')}/query",
        params=params,
        headers=headers,
        timeout=10,
        verify=False,
    )
    r.raise_for_status()
    return r.json()


def _influx_v1_results(data: dict) -> list:
    """Flatten InfluxQL result rows into a flat list of strings."""
    out = []
    for result in data.get("results", []):
        for series in result.get("series", []):
            for row in series.get("values", []):
                if row:
                    out.append(row[0])
    return out


@app.route("/api/influx/scan", methods=["POST"])
def influx_scan():
    """
    Discover InfluxDB structure. Supports v1 (InfluxQL) and v2 (Flux).
    Body:
      url, version ("auto"|"v1"|"v2"), username, password, token, org,
      database (v1) or bucket (v2) to drill into,
      measurement to list fields for.
    """
    body     = request.get_json(force=True) or {}
    conn     = _load_influx_conn()

    url      = (body.get("url")      or conn.get("url", "")).rstrip("/")
    version  = (body.get("version")  or conn.get("version", "auto")).lower()
    username = body.get("username")  or conn.get("username", "")
    # If the frontend sends the masked placeholder, fall back to the stored secret
    raw_password = body.get("password", "")
    password = conn.get("password", "") if raw_password.startswith("•") else (raw_password or conn.get("password", ""))
    raw_token = body.get("token", "")
    token    = conn.get("token", "") if (raw_token.startswith("•") or raw_token.startswith("…")) else (raw_token or conn.get("token", ""))
    org      = body.get("org")       or conn.get("org", "")
    database = body.get("database")  or ""
    bucket   = body.get("bucket")    or ""
    measurement = body.get("measurement") or ""

    if not url:
        return jsonify({"error": "Geen URL opgegeven."}), 400

    # ── Auto-detect version ─────────────────────────────────────────────────
    detected = version
    if version == "auto":
        try:
            r2 = _req.get(f"{url}/api/v2/ping", timeout=5, verify=False)
            if r2.status_code < 400:
                detected = "v2"
            else:
                detected = "v1"
        except Exception:
            try:
                r1 = _req.get(f"{url}/ping", timeout=5, verify=False)
                if r1.status_code < 400:
                    detected = "v1"
                else:
                    return jsonify({"error": f"Geen reactie op {url}"}), 502
            except Exception as exc:
                return jsonify({"error": f"Verbinding mislukt: {exc}"}), 502

    result: dict = {"version": detected, "url": url}

    # ── InfluxDB v1 ──────────────────────────────────────────────────────────
    if detected == "v1":
        try:
            if measurement and database:
                # Fetch field keys and tag keys for this measurement
                fdata = _influx_v1_query(url, username, password,
                                         f'SHOW FIELD KEYS FROM "{measurement}"', db=database)
                tdata = _influx_v1_query(url, username, password,
                                         f'SHOW TAG KEYS FROM "{measurement}"',   db=database)
                fields, tags = [], []
                for res in fdata.get("results", []):
                    for series in res.get("series", []):
                        for row in series.get("values", []):
                            if row:
                                fields.append({"key": row[0], "type": row[1] if len(row) > 1 else "?"})
                for res in tdata.get("results", []):
                    for series in res.get("series", []):
                        for row in series.get("values", []):
                            if row:
                                tags.append(row[0])
                # Also fetch a sample row so user can see entity_id tag values
                sdata = _influx_v1_query(url, username, password,
                                         f'SELECT * FROM "{measurement}" ORDER BY time DESC LIMIT 3',
                                         db=database)
                sample_rows = []
                for res in sdata.get("results", []):
                    for series in res.get("series", []):
                        cols = series.get("columns", [])
                        for row in series.get("values", []):
                            sample_rows.append(dict(zip(cols, row)))
                result.update({"database": database, "measurement": measurement,
                                "fields": fields, "tags": tags, "sample": sample_rows})

            elif database:
                # List measurements in this database
                data = _influx_v1_query(url, username, password,
                                        "SHOW MEASUREMENTS", db=database)
                measurements = _influx_v1_results(data)
                # Also get retention policies
                rpdata = _influx_v1_query(url, username, password,
                                          "SHOW RETENTION POLICIES", db=database)
                rps = []
                for res in rpdata.get("results", []):
                    for series in res.get("series", []):
                        cols = series.get("columns", [])
                        for row in series.get("values", []):
                            rps.append(dict(zip(cols, row)))
                result.update({"database": database,
                                "measurements": measurements,
                                "retention_policies": rps,
                                "measurement_count": len(measurements)})
            else:
                # List databases
                data = _influx_v1_query(url, username, password, "SHOW DATABASES")
                databases = [d for d in _influx_v1_results(data) if not d.startswith("_")]
                result["databases"] = databases

        except Exception as exc:
            return jsonify({"error": f"InfluxDB v1 fout: {exc}"}), 500

    # ── InfluxDB v2 ──────────────────────────────────────────────────────────
    elif detected == "v2":
        if not token:
            return jsonify({"error": "Token vereist voor InfluxDB v2."}), 400
        headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}

        try:
            if measurement and (bucket or database):
                b = bucket or database
                flux = (
                    f'import "influxdata/influxdb/schema"\n'
                    f'schema.measurementFieldKeys(bucket: "{b}", measurement: "{measurement}")'
                )
                flux_tag = (
                    f'import "influxdata/influxdb/schema"\n'
                    f'schema.measurementTagKeys(bucket: "{b}", measurement: "{measurement}")'
                )
                def _flux_query(q):
                    r = _req.post(f"{url}/api/v2/query",
                                  headers=headers, params={"org": org},
                                  json={"query": q, "type": "flux"}, timeout=15, verify=False)
                    r.raise_for_status()
                    rows = []
                    for line in r.text.splitlines():
                        if line.startswith("#") or not line.strip():
                            continue
                        parts = line.split(",")
                        if len(parts) > 3:
                            rows.append(parts[-1].strip())
                    return rows
                fields = [{"key": k, "type": "?"} for k in _flux_query(flux) if k and k != "_value"]
                tags   = [t for t in _flux_query(flux_tag) if t and not t.startswith("_")]
                result.update({"bucket": b, "measurement": measurement,
                                "fields": fields, "tags": tags})

            elif bucket or database:
                b = bucket or database
                flux = (
                    f'import "influxdata/influxdb/schema"\n'
                    f'schema.measurements(bucket: "{b}")'
                )
                r2 = _req.post(f"{url}/api/v2/query",
                               headers=headers, params={"org": org},
                               json={"query": flux, "type": "flux"}, timeout=15, verify=False)
                r2.raise_for_status()
                measurements = []
                for line in r2.text.splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split(",")
                    if len(parts) > 3:
                        val = parts[-1].strip()
                        if val and val != "_value":
                            measurements.append(val)
                result.update({"bucket": b, "measurements": measurements,
                                "measurement_count": len(measurements)})

            else:
                # List buckets
                r2 = _req.get(f"{url}/api/v2/buckets", headers=headers,
                               params={"org": org} if org else {}, timeout=10, verify=False)
                r2.raise_for_status()
                data = r2.json()
                buckets = [{"name": b["name"], "id": b["id"]}
                           for b in data.get("buckets", [])
                           if not b["name"].startswith("_")]
                # Also list orgs
                ro = _req.get(f"{url}/api/v2/orgs", headers=headers, timeout=10, verify=False)
                orgs = [o["name"] for o in ro.json().get("orgs", [])] if ro.ok else []
                result.update({"buckets": buckets, "orgs": orgs})

        except Exception as exc:
            return jsonify({"error": f"InfluxDB v2 fout: {exc}"}), 500

    else:
        return jsonify({"error": f"Onbekende versie: {detected}"}), 400

    return jsonify(result)


# ---------------------------------------------------------------------------
# InfluxDB source mapping  (which fields to use for strategy queries)
# ---------------------------------------------------------------------------

INFLUX_SOURCE_FILE = os.path.join(BASE_DIR, "influx_source.json")


def _load_influx_source() -> dict:
    try:
        with open(INFLUX_SOURCE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.route("/api/influx/source", methods=["GET"])
def get_influx_source():
    return jsonify(_load_influx_source())


@app.route("/api/influx/source", methods=["POST"])
def save_influx_source():
    body = request.get_json(force=True) or {}
    current = _load_influx_source()
    current.update(body)
    with open(INFLUX_SOURCE_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return jsonify({"ok": True})


def _query_external_influx_consumption(days: int = 21) -> list[dict]:
    """
    Query the user-configured external InfluxDB for hourly house consumption.
    Uses influx_source.json mappings.  Supports v1 (InfluxQL) and v2 (Flux).
    Returns [{hour: int, avg_wh: float}] same as query_avg_hourly_consumption().
    """
    src  = _load_influx_source()
    conn = _load_influx_conn()
    if not src.get("mappings"):
        return []

    house_m = src["mappings"].get("house_w")
    if not house_m or not house_m.get("field"):
        return []

    url      = src.get("url") or conn.get("url", "")
    version  = src.get("version") or conn.get("version", "v1")
    database = src.get("database", "")
    username = conn.get("username", "")
    password = conn.get("password", "")
    token    = conn.get("token", "")
    org      = conn.get("org", "")

    if not url:
        return []

    field      = house_m["field"]
    tag_key    = house_m.get("tag_key", "")
    tag_value  = house_m.get("tag_value", "")
    invert     = house_m.get("invert", False)
    scale      = float(house_m.get("scale", 1) or 1)
    meas       = house_m.get("measurement") or src.get("measurement", "")

    sign = -1 if invert else 1

    try:
        tz_name = _entsoe_settings().get("timezone", "Europe/Brussels")

        by_wd_hour: dict[tuple, list] = {}  # (weekday 0=Mon, hour) → [Wh]

        if version == "v1":
            where_parts = []
            if tag_key and tag_value:
                where_parts.append(f'"{tag_key}" = \'{tag_value}\'')
            where_parts.append(f"time > now() - {days}d")
            where_clause = " AND ".join(where_parts)

            q = (f'SELECT mean("{field}") AS val FROM "{meas}"'
                 f' WHERE {where_clause}'
                 f' GROUP BY time(1h) fill(none) tz(\'{tz_name}\')')

            data = _influx_v1_query(url, username, password, q, db=database)
            for res in data.get("results", []):
                for series in res.get("series", []):
                    cols = series.get("columns", [])
                    for row in series.get("values", []):
                        d = dict(zip(cols, row))
                        v = d.get("val") or d.get("mean")
                        if v is None:
                            continue
                        try:
                            t = datetime.fromisoformat(d["time"].replace("Z", "+00:00"))
                            t_local = t.astimezone(ZoneInfo(tz_name))
                            key = (t_local.weekday(), t_local.hour)
                            by_wd_hour.setdefault(key, []).append(float(v) * sign * scale)
                        except Exception:
                            pass

        elif version == "v2":
            if not token:
                return []
            headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
            tag_filter = f' |> filter(fn: (r) => r["{tag_key}"] == "{tag_value}")' if tag_key and tag_value else ""
            flux = (
                f'from(bucket: "{database}")\n'
                f'  |> range(start: -{days}d)\n'
                f'  |> filter(fn: (r) => r._measurement == "{meas}" and r._field == "{field}"){tag_filter}\n'
                f'  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n'
                f'  |> map(fn: (r) => ({{r with _value: r._value * {sign * scale}}}))'
            )
            r = _req.post(f"{url.rstrip('/')}/api/v2/query",
                          headers=headers, params={"org": org},
                          json={"query": flux, "type": "flux"}, timeout=30, verify=False)
            r.raise_for_status()
            for line in r.text.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                try:
                    t = datetime.fromisoformat(parts[5].strip().replace("Z", "+00:00"))
                    t_local = t.astimezone(ZoneInfo(tz_name))
                    v = float(parts[-1].strip())
                    key = (t_local.weekday(), t_local.hour)
                    by_wd_hour.setdefault(key, []).append(v)
                except Exception:
                    pass
        else:
            return []

        result = []
        for (wd, hour), vals in sorted(by_wd_hour.items()):
            result.append({"weekday": wd, "hour": hour, "avg_wh": round(sum(vals) / len(vals), 1)})
        log.info("External InfluxDB consumption: %d (weekday, hour) buckets from %s", len(result), url)
        return result

    except Exception as exc:
        log.warning("External InfluxDB consumption query failed: %s", exc)
        return []


def _query_external_influx_slot_latest(slot_key: str) -> list[float]:
    """
    Fetch the most recent value(s) for a slot from the external InfluxDB.
    Returns a list of floats (one per battery entry for multi slots, one item otherwise).
    Used to get current bat_soc from external InfluxDB when ESPHome/Marstek InfluxDB has no data.
    """
    src  = _load_influx_source()
    conn = _load_influx_conn()
    if not src.get("mappings"):
        return []

    raw = src["mappings"].get(slot_key)
    if not raw:
        return []

    # Normalise to list
    entries = raw if isinstance(raw, list) else [raw]
    entries = [e for e in entries if e.get("field")]
    if not entries:
        return []

    url      = src.get("url") or conn.get("url", "")
    version  = src.get("version") or conn.get("version", "v1")
    database = src.get("database", "")
    username = conn.get("username", "")
    password = conn.get("password", "")
    token    = conn.get("token", "")
    org      = conn.get("org", "")

    if not url:
        return []

    results = []
    for entry in entries:
        field     = entry["field"]
        tag_key   = entry.get("tag_key", "")
        tag_value = entry.get("tag_value", "")
        invert    = entry.get("invert", False)
        scale     = float(entry.get("scale", 1) or 1)
        meas      = entry.get("measurement") or src.get("measurement", "")
        sign      = -1 if invert else 1
        try:
            if version == "v1":
                where_parts = []
                if tag_key and tag_value:
                    where_parts.append(f'"{tag_key}" = \'{tag_value}\'')
                where_str = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
                q = f'SELECT last("{field}") FROM "{meas}"{where_str}'
                data = _influx_v1_query(url, username, password, q, db=database)
                for res in data.get("results", []):
                    for series in res.get("series", []):
                        for row in series.get("values", []):
                            v = row[1] if len(row) > 1 else None
                            if v is not None:
                                results.append(float(v) * sign * scale)
            elif version == "v2":
                if not token:
                    continue
                headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
                tag_filter = f' |> filter(fn: (r) => r["{tag_key}"] == "{tag_value}")' if tag_key and tag_value else ""
                flux = (
                    f'from(bucket: "{database}")\n'
                    f'  |> range(start: -1h)\n'
                    f'  |> filter(fn: (r) => r._measurement == "{meas}" and r._field == "{field}"){tag_filter}\n'
                    f'  |> last()'
                )
                r = _req.post(f"{url.rstrip('/')}/api/v2/query",
                              headers=headers, params={"org": org},
                              json={"query": flux, "type": "flux"}, timeout=10, verify=False)
                r.raise_for_status()
                for line in r.text.splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split(",")
                    if len(parts) < 4:
                        continue
                    try:
                        v = float(parts[-1].strip())
                        results.append(v * sign * scale)
                        break
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("External InfluxDB slot latest [%s] failed: %s", slot_key, exc)

    return results


@app.route("/api/influx/live-slots")
def get_influx_live_slots():
    """Return the latest value for each configured InfluxDB slot (for live power-flow display)."""
    # bat_soc → average of all batteries; everything else → sum
    AVG_SLOTS = {"bat_soc"}
    result = {}
    for slot in ("house_w", "solar_w", "net_w", "bat_soc", "bat_w"):
        try:
            vals = _query_external_influx_slot_latest(slot)
            if vals:
                result[slot] = sum(vals) / len(vals) if slot in AVG_SLOTS else sum(vals)
        except Exception as exc:
            log.debug("live-slots [%s]: %s", slot, exc)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Serve React frontend (production build)
# ---------------------------------------------------------------------------

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    dist = os.path.abspath(FRONTEND_DIST)
    if not os.path.isdir(dist):
        return jsonify({"error": "Frontend not built. Run 'npm run build' in frontend/."}), 404

    full_path = os.path.join(dist, path)
    if path and os.path.isfile(full_path):
        return send_from_directory(dist, path)

    index = os.path.join(dist, "index.html")
    if os.path.isfile(index):
        # When running behind HA ingress the browser sees a path like
        # /api/hassio_ingress/TOKEN/ – inject a <base> tag so all relative
        # fetch("api/...") calls resolve through the ingress proxy.
        ingress_path = request.headers.get("X-Ingress-Path", "")
        if ingress_path:
            base_href = ingress_path.rstrip("/") + "/"
            with open(index, encoding="utf-8") as f:
                html = f.read()
            html = html.replace("<head>", f'<head>\n    <base href="{base_href}">', 1)
            return Response(html, mimetype="text/html")
        return send_from_directory(dist, "index.html")

    abort(404)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _influx_context():
    """Collect current data for the InfluxDB background writer."""
    devices  = load_devices()
    flow_cfg: dict = {}
    try:
        with open(FLOW_CFG_SERVER_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for key, val in raw.items():
            if isinstance(val, list):
                flow_cfg[key] = val
            elif isinstance(val, dict):
                flow_cfg[key] = [val]
    except Exception:
        pass

    # Poll HomeWizard
    hw_data = None
    try:
        hw_devs = {}
        for dev_id, dev in (json.load(open(os.path.join(BASE_DIR, "homewizard_devices.json")))
                            if os.path.exists(os.path.join(BASE_DIR, "homewizard_devices.json"))
                            else {}).items():
            selected = dev.get("selected_sensors") or []
            try:
                r = _req.get(f"http://{dev['ip']}/api", timeout=3)
                if r.ok:
                    data = r.json()
                    sensors = {}
                    for k, v in data.items():
                        if k in selected and isinstance(v, (int, float)):
                            meta = _hw_sensor_meta(k)
                            sensors[k] = {"label": meta["label"], "unit": meta["unit"], "value": v}
                    hw_devs[dev_id] = {"id": dev_id, "name": dev.get("name",""), "sensors": sensors}
            except Exception:
                pass
        if hw_devs:
            hw_data = {"devices": list(hw_devs.values())}
    except Exception:
        pass

    # Poll HA for configured sensors
    ha_data: dict = {}
    ha_s = _ha_effective_settings()
    if ha_s.get("token") and ha_s.get("url"):
        ha_entity_ids = list({
            sc["sensor"]
            for entries in flow_cfg.values()
            for sc in (entries if isinstance(entries, list) else [entries])
            if sc.get("source") == "homeassistant" and sc.get("sensor")
        })
        if ha_entity_ids:
            headers = _ha_headers(ha_s["token"])
            def _fetch_ha(eid):
                try:
                    r = _req.get(f"{ha_s['url']}/api/states/{eid}",
                                 headers=headers, timeout=4, verify=False)
                    if r.ok:
                        d = r.json()
                        try:
                            v = float(d.get("state",""))
                        except Exception:
                            v = None
                        return eid, {"value": v, "unit": d.get("attributes",{}).get("unit_of_measurement","")}
                except Exception:
                    pass
                return eid, None
            with ThreadPoolExecutor(max_workers=6) as pool:
                for eid, val in pool.map(_fetch_ha, ha_entity_ids):
                    if val is not None:
                        ha_data[eid] = val

    return {"devices": devices, "hw_data": hw_data, "ha_data": ha_data, "flow_cfg": flow_cfg}


# ---------------------------------------------------------------------------
# Automation – automatically apply strategy actions to batteries
# ---------------------------------------------------------------------------

AUTOMATION_FILE = os.path.join(BASE_DIR, "automation.json")

def _compute_forward_plan(force_claude: bool = False) -> dict:
    """
    Build the forward charging plan (today + tomorrow).
    Fetches fresh prices, solar forecast, consumption profile and live SoC.
    Updates _plan_cache and returns the result dict.
    Called both by the /api/strategy/plan route and by the automation thread
    when the cached plan is stale.

    force_claude=True bypasses the price-fingerprint cache check for the Claude
    engine (used when the user explicitly clicks Vernieuwen).
    """
    s   = load_strategy_settings()
    es  = _entsoe_settings()
    fs  = _forecast_settings()
    tz_name   = es.get("timezone", "Europe/Brussels")
    today_str = date.today().isoformat()

    # ── Parallel fetch: prices, solar, consumption, SoC ──────────────────
    # All four are independent I/O operations — run them concurrently.

    price_source     = s.get("price_source", "entsoe")
    history_days     = s.get("history_days", 21)
    cons_source_pref = s.get("consumption_source", "auto")
    ext_src          = _load_influx_source()
    ext_configured   = bool(ext_src.get("mappings") and ext_src.get("database"))

    def _do_prices():
        src = price_source
        rows: list = []
        if src == "frank":
            frank_sess    = _frank_session()
            frank_token   = frank_sess.get("authToken")
            frank_country = frank_sess.get("country", "BE")
            if frank_token or frank_country == "BE":
                try:
                    target_d = date.fromisoformat(today_str)
                    for d_off in [0, 1]:
                        day = target_d + timedelta(days=d_off)
                        frank_rows = _fetch_prices(frank_token, day,
                                                   day + timedelta(days=1), frank_country)
                        for row in frank_rows:
                            all_in = (
                                (row.get("marketPrice")         or 0.0) +
                                (row.get("marketPriceTax")      or 0.0) +
                                (row.get("sourcingMarkupPrice") or 0.0) +
                                (row.get("energyTaxPrice")      or 0.0)
                            )
                            rows.append({"from": row["from"], "till": row["till"],
                                         "marketPrice": all_in})
                except Exception as exc:
                    log.warning("_compute_forward_plan: Frank fetch error: %s", exc)
            if not rows:
                log.warning("_compute_forward_plan: Frank prices empty – falling back to ENTSO-E")
                src = "entsoe"
        if src == "entsoe" and es.get("apiKey"):
            try:
                target_d = date.fromisoformat(today_str)
                rows += _fetch_entsoe_day(es["apiKey"], target_d,
                                          es.get("country", "BE"), tz_name)
                rows += _fetch_entsoe_day(es["apiKey"], target_d + timedelta(days=1),
                                          es.get("country", "BE"), tz_name)
            except Exception as exc:
                log.warning("_compute_forward_plan: ENTSO-E fetch error: %s", exc)
        return rows, src

    def _do_solar():
        if fs.get("lat") and fs.get("lon"):
            try:
                return _fetch_forecast(fs).get("watt_hours_period", {})
            except Exception as exc:
                log.warning("_compute_forward_plan: forecast fetch error: %s", exc)
        return {}

    def _do_consumption():
        # Cache key: settings that affect the profile
        cache_key = f"{cons_source_pref}:{history_days}:{tz_name}"
        ca = _consumption_cache.get("fetched_at")
        if (ca and _consumption_cache.get("key") == cache_key
                and _consumption_cache.get("data")):
            age_s = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(ca)).total_seconds()
            if age_s < _CONSUMPTION_CACHE_TTL:
                log.debug("_compute_forward_plan: consumption from cache (%ds old)", int(age_s))
                return _consumption_cache["data"], _consumption_cache["source"]

        def _try_external():
            if ext_configured:
                data = _query_external_influx_consumption(days=history_days)
                if len(data) >= 18:
                    return data, "external_influx"
            return [], ""

        def _try_local():
            data = query_avg_hourly_consumption(days=history_days, tz_name=tz_name)
            if len(data) >= 18:
                return data, "local_influx"
            return [], ""

        def _try_ha():
            data = _query_ha_hourly_consumption(days=history_days)
            if data:
                return data, "ha_history"
            return [], ""

        if cons_source_pref == "external_influx":
            d, src = _try_external()
        elif cons_source_pref == "local_influx":
            d, src = _try_local()
        elif cons_source_pref == "ha_history":
            d, src = _try_ha()
        else:
            d, src = [], ""
            for _fn in (_try_external, _try_local, _try_ha):
                d, src = _fn()
                if d:
                    break

        if d:
            _consumption_cache["data"]       = d
            _consumption_cache["source"]     = src
            _consumption_cache["fetched_at"] = datetime.now(timezone.utc).isoformat()
            _consumption_cache["key"]        = cache_key
        return d, src

    def _do_soc():
        # Simply reuse _live_soc() which tries all sources in order:
        # last_soc.json → flow-slot poll (ESPHome+HA) → ESPHome direct.
        # Then fall back to InfluxDB queries if that also returns None.
        soc = _live_soc()
        if soc is not None:
            log.info("_compute_forward_plan: SoC from live poll: %.1f%%", soc)
            return soc
        # External InfluxDB (HA-side)
        if ext_configured:
            try:
                ext_socs = _query_external_influx_slot_latest("bat_soc")
                if ext_socs:
                    soc = sum(ext_socs) / len(ext_socs)
                    log.info("_compute_forward_plan: SoC from external InfluxDB: %.1f%%", soc)
            except Exception:
                pass
        # Local InfluxDB
        if soc is None:
            try:
                recent = query_recent_points(hours=1)
                soc = next((p["bat_soc"] for p in reversed(recent) if "bat_soc" in p), None)
                if soc is not None:
                    log.info("_compute_forward_plan: SoC from local InfluxDB: %.1f%%", soc)
            except Exception:
                pass
        if soc is None:
            log.warning("_compute_forward_plan: SoC onbekend uit alle bronnen – fallback 50%%")
        return soc if soc is not None else 50.0

    with ThreadPoolExecutor(max_workers=4) as _ex:
        _f_prices = _ex.submit(_do_prices)
        _f_solar  = _ex.submit(_do_solar)
        _f_cons   = _ex.submit(_do_consumption)
        _f_soc    = _ex.submit(_do_soc)
        prices,              price_source      = _f_prices.result()
        solar_wh                               = _f_solar.result()
        consumption_by_hour, consumption_source = _f_cons.result()
        soc_now                                = _f_soc.result()

    log.info("_compute_forward_plan: consumption=%s (%d slots), prices=%d, solar=%d, SoC=%.1f%%",
             consumption_source, len(consumption_by_hour), len(prices), len(solar_wh), soc_now)

    # ── Price fingerprint (for Claude cache invalidation) ─────────────────
    # Hash the sorted "from" timestamps of all price slots. Changes only when
    # new prices are published (once per day, ~14:00 for next day).
    import hashlib as _hashlib
    _price_fp = _hashlib.md5(
        json.dumps(sorted(p.get("from", "") for p in prices)).encode()
    ).hexdigest()[:12]

    # ── Build plan & update cache ─────────────────────────────────────────
    _claude_debug = None
    if s.get("strategy_mode") == "claude":
        # Only call Claude when prices have actually changed.
        # Serve cached plan otherwise (price-fingerprint based, not time-based).
        _cached_fp  = _plan_cache.get("price_fingerprint")
        _have_cache = bool(_plan_cache.get("result"))
        if not force_claude and _have_cache and _cached_fp == _price_fp:
            log.info("_compute_forward_plan: Claude mode – prices unchanged (fp=%s), serving cache",
                     _price_fp)
            # Update SoC in cached result so the UI reflects current battery level
            _plan_cache["result"]["soc_now"] = soc_now
            return _plan_cache["result"]

        log.info("_compute_forward_plan: Claude mode – %s (fp %s→%s)",
                 "forced refresh" if force_claude else "new prices",
                 _cached_fp or "none", _price_fp)
        try:
            import strategy_claude as _sc_mod
            plan = _sc_mod.build_plan_claude(prices, solar_wh, consumption_by_hour, soc_now, s)
            _claude_debug = _sc_mod.get_last_debug()
            log.info("_compute_forward_plan: Claude engine done  fallback=%s",
                     _claude_debug.get("fallback"))
        except Exception as _ce:
            log.warning("_compute_forward_plan: Claude engine failed (%s) — rule-based fallback", _ce)
            plan = build_plan(prices, solar_wh, consumption_by_hour, soc_now, s)
    else:
        plan = build_plan(prices, solar_wh, consumption_by_hour, soc_now, s)
    result = split_days(plan)
    result["consumption_by_hour"] = consumption_by_hour
    result["prices_available"]    = len(prices) > 0
    result["solar_available"]     = len(solar_wh) > 0
    result["soc_now"]             = soc_now
    result["is_historical"]       = False
    result["consumption_source"]  = consumption_source
    result["price_source_used"]   = price_source
    result["strategy_engine"]     = s.get("strategy_mode", "rule_based")
    result["price_fingerprint"]   = _price_fp
    if _claude_debug:
        result["claude_debug"]    = _claude_debug
    # Expose calculated (or configured) standby for display in the UI
    from strategy import load_strategy_settings as _lss
    _ss = _lss()
    _cfg_standby = float(_ss.get("standby_w", 0))
    if _cfg_standby > 0:
        result["standby_w"] = _cfg_standby
    else:
        # Re-derive from consumption profile (mirrors strategy.py logic)
        _sv = [x["avg_wh"] for x in consumption_by_hour
               if x.get("hour") in {2, 3, 4, 5} or
                  (x.get("weekday") is not None and x.get("hour") in {2, 3, 4, 5})]
        result["standby_w"] = round(sum(_sv) / len(_sv), 0) if _sv else 0

    _plan_cache["slots"]             = result.get("all", [])
    _plan_cache["fetched_at"]        = datetime.now(timezone.utc).isoformat()
    _plan_cache["result"]            = result
    _plan_cache["price_fingerprint"] = _price_fp
    _persist_plan_cache()
    return result


# Cache the last computed strategy plan (set by _compute_forward_plan)
_plan_cache: dict = {"slots": [], "fetched_at": None, "result": None}

# Persist plan cache to disk so Claude is not re-called after addon restarts.
_PLAN_CACHE_FILE = os.path.join(DATA_DIR, "strategy_plan_cache.json")


def _persist_plan_cache() -> None:
    """Save _plan_cache to disk (called after every successful plan build)."""
    try:
        with open(_PLAN_CACHE_FILE, "w", encoding="utf-8") as _f:
            json.dump({
                "fetched_at":        _plan_cache.get("fetched_at"),
                "price_fingerprint": _plan_cache.get("price_fingerprint"),
                "result":            _plan_cache.get("result"),
                # slots list can be large; derive from result["all"] on restore
            }, _f)
    except Exception as _e:
        log.warning("_persist_plan_cache: write failed: %s", _e)


def _restore_plan_cache() -> None:
    """Load _plan_cache from disk on startup (only if ≤ 26 h old)."""
    try:
        with open(_PLAN_CACHE_FILE, "r", encoding="utf-8") as _f:
            data = json.load(_f)
        fetched_at = data.get("fetched_at")
        if not fetched_at:
            return
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(fetched_at)).total_seconds() / 3600
        if age_h > 26:
            log.info("_restore_plan_cache: cache too old (%.1fh) – discarded", age_h)
            return
        _plan_cache["fetched_at"]        = fetched_at
        _plan_cache["price_fingerprint"] = data.get("price_fingerprint")
        _plan_cache["result"]            = data.get("result")
        _plan_cache["slots"]             = (data.get("result") or {}).get("all", [])
        log.info("_restore_plan_cache: restored (age=%.1fh, fp=%s)",
                 age_h, data.get("price_fingerprint"))
    except FileNotFoundError:
        pass
    except Exception as _e:
        log.warning("_restore_plan_cache: read failed: %s", _e)

# Consumption profile cache — valid for 30 minutes (profile changes slowly)
_consumption_cache: dict = {"data": [], "source": "", "fetched_at": None, "key": ""}
_CONSUMPTION_CACHE_TTL = 1800

# action → list of (domain, entity_name, value) commands
# U+2044 FRACTION SLASH is used in ESPHome entity name "Forcible Charge⁄Discharge"
_FORCIBLE = "Marstek Forcible Charge\u2044Discharge"
_AUTOMATION_MODES: dict[str, list] = {
    "solar_charge": [
        ("select", "Marstek User Work Mode", "anti-feed"),
        ("select", _FORCIBLE,                "stop"),
    ],
    "grid_charge": [
        ("select", "Marstek User Work Mode", "manual"),
        ("select", _FORCIBLE,                "charge"),
    ],
    "save": [
        ("select", "Marstek User Work Mode", "anti-feed"),
        ("select", _FORCIBLE,                "stop"),
    ],
    "discharge": [
        ("select", "Marstek User Work Mode", "anti-feed"),
        ("select", _FORCIBLE,                "stop"),
    ],
    "neutral": [
        ("select", "Marstek User Work Mode", "anti-feed"),
        ("select", _FORCIBLE,                "stop"),
    ],
}


def _read_live_flow_slots(*slot_keys: str) -> dict[str, float | None]:
    """Read one or more flow_cfg slots live (ESPHome + HA sources).
    Returns {slot_key: value_or_None}."""
    try:
        from influx_writer import _poll_esphome, _resolve_slot
        devices_dict = load_devices()
        live_cfg: dict = {}
        try:
            with open(FLOW_CFG_SERVER_FILE, "r", encoding="utf-8") as _f:
                raw = json.load(_f)
            for k, v in raw.items():
                live_cfg[k] = v if isinstance(v, list) else [v]
        except Exception:
            return {k: None for k in slot_keys}

        esphome_map = _poll_esphome(devices_dict)

        # Collect all HA entity IDs needed across requested slots
        ha_eids: list[str] = []
        for slot_key in slot_keys:
            ha_eids += [e["sensor"] for e in live_cfg.get(slot_key, [])
                        if e.get("source") == "homeassistant" and e.get("sensor")]

        ha_data: dict = {}
        ha_s = _ha_effective_settings()
        if ha_eids and ha_s.get("token") and ha_s.get("url"):
            for eid in set(ha_eids):
                try:
                    r = _req.get(f"{ha_s['url']}/api/states/{eid}",
                                 headers=_ha_headers(ha_s["token"]),
                                 timeout=3, verify=False)
                    if r.ok:
                        ha_data[eid] = {"value": float(r.json().get("state", "nan"))}
                except Exception:
                    pass

        return {k: _resolve_slot(k, live_cfg, esphome_map, None, ha_data)
                for k in slot_keys}
    except Exception as exc:
        log.debug("_read_live_flow_slots failed: %s", exc)
        return {k: None for k in slot_keys}


def _solar_overproduction_w() -> float | None:
    """Return how many watts solar exceeds house consumption right now.
    Positive = overproduction, negative = solar < consumption, None = unknown.
    Priority:
      1. house_power slot (direct HA sensor) + solar_power slot
      2. solar_power + net_power: house = solar + net  (independent of battery mode)
      3. solar_power + consumption profile from plan cache
    """
    slots   = _read_live_flow_slots("solar_power", "net_power", "house_power")
    solar_w = slots.get("solar_power")
    net_w   = slots.get("net_power")
    house_w = slots.get("house_power")

    if solar_w is None:
        return None

    # 1. Direct house sensor configured
    if house_w is not None:
        return solar_w - house_w

    # 2. Derive house from solar + net (net independent of battery mode)
    if net_w is not None:
        return solar_w - (solar_w + net_w)   # = -net_w

    # 3. Fall back to consumption profile for this hour
    try:
        tz_name = _entsoe_settings().get("timezone", "Europe/Brussels")
        now_h   = datetime.now(ZoneInfo(tz_name)).hour
        cons_by_hour = _plan_cache.get("consumption_by_hour", {})
        profile_w = cons_by_hour.get(now_h) or cons_by_hour.get(str(now_h))
        if profile_w is not None:
            return solar_w - float(profile_w)
    except Exception:
        pass
    return None


def _live_soc() -> float | None:
    """Return the most recent battery SOC, trying multiple sources in order.

    1. last_soc.json  – written every ~30 s by the data collector (fastest)
    2. _read_live_flow_slots("bat_soc") – direct ESPHome + HA poll (always fresh)
    3. ESPHome direct poll – works even when bat_soc not in flow_cfg
    """
    # 1. last_soc.json (< 5 min old)
    try:
        _soc_file = os.path.join(DATA_DIR, "last_soc.json")
        with open(_soc_file, encoding="utf-8") as _f:
            _sc = json.load(_f)
        if time.time() - _sc.get("ts", 0) < 300:
            return float(_sc["soc"])
    except Exception:
        pass

    # 2. Live poll via configured flow sources
    try:
        val = _read_live_flow_slots("bat_soc").get("bat_soc")
        if val is not None:
            log.debug("_live_soc: from flow poll: %.1f%%", val)
            return float(val)
    except Exception:
        pass

    # 3. Direct ESPHome poll (no flow_cfg needed)
    try:
        from influx_writer import _poll_esphome
        esphome_map = _poll_esphome(load_devices())
        raw_socs = [v["soc"] for v in esphome_map.values() if "soc" in v]
        if raw_socs:
            soc = sum(raw_socs) / len(raw_socs)
            log.debug("_live_soc: from ESPHome direct poll: %.1f%%", soc)
            return soc
    except Exception:
        pass

    return None


def _check_solar_deficit_save() -> str | None:
    """
    Returns an override-reason string when solar production is significantly
    below the hourly forecast AND a discharge slot is coming up within 14 h.
    Used to switch solar_charge / neutral → save so the battery SOC is
    preserved for the discharge instead of draining away in neutral mode.
    Returns None when no override is warranted.
    """
    slots = _plan_cache.get("slots", [])
    if not slots:
        return None

    tz_name = _entsoe_settings().get("timezone", "Europe/Brussels")
    now_h = datetime.now(ZoneInfo(tz_name)).hour

    current_slot = next((s for s in slots if s.get("hour") == now_h), None)
    if current_slot is None:
        return None

    # Only act when this hour had a meaningful solar forecast
    forecast_wh = float(current_slot.get("solar_wh", 0) or 0)
    if forecast_wh < 300:
        return None

    # Actual live solar production (W ≈ Wh for the current hour)
    live = _read_live_flow_slots("solar_power")
    actual_w = live.get("solar_power")
    if actual_w is None:
        return None   # no solar sensor configured – can't judge

    # Trigger when actual is < 25 % of forecast (≥ 75 % shortfall)
    if actual_w >= forecast_wh * 0.25:
        return None

    # Is there a discharge slot in the next 14 hours?
    has_upcoming_discharge = any(
        s.get("action") == "discharge"
        for s in slots
        if 0 < ((s.get("hour", 0) - now_h) % 24) <= 14
    )
    if not has_upcoming_discharge:
        return None

    # No point saving when battery is already near-full
    soc = _live_soc()
    if soc is None or soc >= 85:
        return None

    return (
        f"⛅ Zontekort: {actual_w:.0f}W actueel vs "
        f"{forecast_wh:.0f}Wh voorspeld – SOC {soc:.0f}% bewaren voor ontlading"
    )


def _load_automation() -> dict:
    try:
        with open(AUTOMATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "last_action": None, "last_applied": None}


def _save_automation(data: dict) -> None:
    with open(AUTOMATION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _current_slot_action() -> str | None:
    """Return the action for the current hour from the cached plan."""
    slots = _plan_cache.get("slots", [])
    if not slots:
        return None
    tz_name = _entsoe_settings().get("timezone", "Europe/Brussels")
    now_local = datetime.now(ZoneInfo(tz_name))
    for slot in slots:
        if slot.get("hour") == now_local.hour:
            return slot.get("action", "neutral")
    return "neutral"


def _automation_tick() -> None:
    """
    Run once per minute. Only sends commands when the action changes
    (i.e. at an hour boundary), avoiding constant chatter to the devices.
    Also re-applies on first enable and when automation is re-enabled.
    """
    auto = _load_automation()
    if not auto.get("enabled"):
        return

    # ── Auto-refresh plan when stale ─────────────────────────────────────────
    # For Claude mode: only refresh when prices have changed (fingerprint).
    # Time-based hourly refresh would re-call Claude every hour which costs money.
    # For rule-based: refresh hourly so solar forecast + SoC stay current.
    fetched_at_str = _plan_cache.get("fetched_at")
    plan_stale = fetched_at_str is None
    s_now = load_strategy_settings()
    if not plan_stale:
        age_s = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at_str)).total_seconds()
        if s_now.get("strategy_mode") == "claude":
            # Claude: stale only when no cache at all; fingerprint check inside
            # _compute_forward_plan() will skip the actual Claude call if prices
            # haven't changed, but still costs a prices+solar fetch. Only trigger
            # when cache is >25h old (prices would definitely have changed).
            plan_stale = age_s >= 90000   # 25 hours
        else:
            plan_stale = age_s >= 3600    # rule-based: refresh every hour
    if plan_stale:
        log.info("Automation: plan stale (age=%.0fs, engine=%s) – recomputing…",
                 (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at_str)).total_seconds()
                 if fetched_at_str else 0,
                 s_now.get("strategy_mode", "rule_based"))
        try:
            _compute_forward_plan()
        except Exception as exc:
            log.warning("Automation: strategy refresh failed: %s", exc)

    action = _current_slot_action()
    if action is None:
        log.debug("Automation: no plan cached yet – skipping")
        return

    # ── Solar overproduction override ────────────────────────────────────────
    # If the plan says "save" (battery passive) but solar production currently
    # exceeds house consumption, switch to anti-feed (neutral) so the battery
    # absorbs the surplus. Uses solar_power vs house load — independent of
    # battery mode, so no oscillation when anti-feed drives net back to ~0.
    override_reason: str | None = None
    effective_action = action
    if action == "save":
        surplus_w = _solar_overproduction_w()
        if surplus_w is not None and surplus_w > 50:   # >50 W surplus → absorb
            effective_action = "neutral"
            override_reason  = f"☀️ Zonne-overproductie ({surplus_w:.0f} W) – anti-feed actief"
            log.info("Automation: save→neutral override (solar surplus=%.0fW)", surplus_w)

    # ── Solar-deficit failsafe ────────────────────────────────────────────────
    # When the plan expected solar production (solar_charge / neutral) but the
    # sun isn't delivering, switch to save so the current SOC is preserved for
    # any discharge slot later in the day.
    if effective_action in ("solar_charge", "neutral") and override_reason is None:
        deficit_reason = _check_solar_deficit_save()
        if deficit_reason:
            effective_action = "save"
            override_reason = deficit_reason
            log.info("Automation: solar-deficit failsafe → save (%s)", deficit_reason)

    prev_action = auto.get("last_action")
    if effective_action == prev_action:
        log.debug("Automation: action unchanged (%s) – no commands sent", effective_action)
        # Still update override_reason in state so the UI can reflect live status
        if auto.get("override_reason") != override_reason:
            auto["override_reason"] = override_reason
            _save_automation(auto)
        return

    base_commands = list(_AUTOMATION_MODES.get(effective_action, _AUTOMATION_MODES["neutral"]))
    devices       = load_devices()

    if not devices:
        log.debug("Automation: no devices configured")
        return

    # For grid_charge: also set Forcible Charge Power (W per battery) and Charge to SoC.
    # max_charge_kw is the total grid charge power across all batteries.
    # Divide evenly across devices; Marstek expects watts as an integer.
    extra_commands: list[tuple] = []
    if action == "grid_charge":
        s_now      = load_strategy_settings()
        num_dev    = len(devices)
        total_w    = float(s_now.get("max_charge_kw", 3.0)) * 1000.0
        per_bat_w  = int(round(total_w / num_dev))
        charge_soc = int(s_now.get("max_soc", 95))
        extra_commands = [
            ("number", "Marstek Forcible Charge Power",    str(per_bat_w)),
            ("number", "Marstek Charge to SoC",            str(charge_soc)),
        ]
        log.info("Automation: grid_charge  total=%.1fkW  per_battery=%dW  charge_to_soc=%d%%  devices=%d",
                 total_w / 1000, per_bat_w, charge_soc, num_dev)

    commands = base_commands + extra_commands

    log.info("Automation: action changed %s → %s%s  (devices=%d)",
             prev_action or "none", effective_action,
             f" [override from {action}]" if override_reason else "", len(devices))
    for device_id, device in devices.items():
        for domain, name, value in commands:
            result = send_esphome_command(device["ip"], device["port"], domain, name, value)
            if result.get("ok"):
                log.info("Automation: ✓ %s → %s=%s  (device %s)", effective_action, name, value, device_id)
            else:
                log.warning("Automation: ✗ %s → %s=%s  (device %s): %s",
                            effective_action, name, value, device_id, result.get("error"))

    auto["last_action"]    = effective_action   # what was actually sent to devices
    auto["planned_action"] = action             # what the strategy intended
    auto["override_reason"] = override_reason   # non-None when override active
    auto["last_applied"]   = datetime.now(timezone.utc).isoformat()

    # ── PV power limiter (e.g. SMA Sunny Boy via HA) ─────────────────────────
    # At negative/very cheap prices: curtail PV to avoid costly export.
    # At negative prices you also want to grid_charge (the strategy handles that),
    # but solar export at negative price costs money → limit PV output.
    _apply_pv_limiter(s_now, auto)

    _save_automation(auto)


def _pv_send(s: dict, entity: str, use_service: bool, svc_str: str, target_w: int) -> bool:
    """Send target_w to HA via entity mode or service mode."""
    if use_service:
        param_key = (s.get("pv_limiter_service_param_key") or "entity_id").strip()
        svc_param = (s.get("pv_limiter_service_param") or "").strip()
        if "." not in svc_str:
            return False
        domain, svc = svc_str.split(".", 1)
        data: dict = {"value": target_w}
        if svc_param and param_key:
            data[param_key] = svc_param
        return _ha_call_service(domain, svc, data)
    else:
        if not entity:
            return False
        # sensor.* entities can't use number.set_value — auto-switch to service mode
        # when a service string is configured (e.g. pysmaplus.set_value)
        if entity.startswith("sensor.") and svc_str and "." in svc_str:
            param_key = (s.get("pv_limiter_service_param_key") or "entity_id").strip()
            domain, svc = svc_str.split(".", 1)
            data: dict = {"value": target_w, param_key: entity}
            return _ha_call_service(domain, svc, data)
        return _ha_call_service("number", "set_value", {"entity_id": entity, "value": target_w})


def _apply_pv_limiter(s: dict, auto: dict) -> None:
    """
    Set or restore the HA PV power limit entity based on the current price.

    At negative/cheap prices the target is calculated dynamically so that
    house consumption + battery charging can happen internally but nothing
    flows back to the grid:

        target_w = house_consumption_w + battery_charge_w + margin_w

    - house_consumption_w: estimated from current plan slot (Wh ≈ W mean)
    - battery_charge_w   : max_charge_kw × 1000 when battery < max_soc
    - margin_w           : small buffer to avoid oscillation (default 200 W)

    When price ≥ threshold: restore to pv_limiter_max_w (full output).
    Only sends an HA command when the target value changes.
    """
    entity      = (s.get("pv_limiter_entity") or "").strip()
    use_service = s.get("pv_limiter_use_service")
    svc_str     = (s.get("pv_limiter_service") or "").strip()

    if not s.get("pv_limiter_enabled"):
        # Limiter disabled: restore to max_w if we previously curtailed
        last_w = auto.get("pv_limiter_last_w")
        if last_w is None:
            return  # never set anything, nothing to restore
        max_w = int(s.get("pv_limiter_max_w", 4000))
        if last_w == max_w:
            auto.pop("pv_limiter_last_w", None)
            return
        _pv_send(s, entity, use_service, svc_str, max_w)
        auto.pop("pv_limiter_last_w", None)
        return

    # In entity mode, entity is required; in service mode, the service string is required
    if not use_service and not entity:
        return
    if use_service and not svc_str:
        return

    max_w     = int(s.get("pv_limiter_max_w",         4000))
    threshold = float(s.get("pv_limiter_threshold_ct", 0.0)) / 100.0  # € /kWh
    margin_w  = int(s.get("pv_limiter_margin_w",        200))

    # Find current slot price, consumption estimate, battery SOC and action from plan
    tz_name     = s.get("timezone", "Europe/Brussels")
    now_local   = datetime.now(ZoneInfo(tz_name))
    slots       = _plan_cache.get("slots", [])
    price       = None
    cons_w      = 300.0   # fallback: average house load estimate
    soc_pct     = None
    slot_action = None    # planned action for current hour
    for sl in slots:
        try:
            sl_dt = datetime.fromisoformat(sl["time"])
            if sl_dt.hour == now_local.hour and sl_dt.date() == now_local.date():
                price       = sl.get("price_eur_kwh")
                cons_w      = float(sl.get("consumption_wh") or cons_w)
                soc_pct     = sl.get("soc_start")
                slot_action = sl.get("action")
                break
        except Exception:
            pass

    # Fallback: read directly from price cache when no plan is available
    if price is None:
        today_key = now_local.date().isoformat()
        cached    = _price_cache.get(today_key)
        if cached:
            rows       = (cached.get("data") or {}).get("today", [])
            price_src  = s.get("price_source", "entsoe")
            markup     = 0.0 if price_src == "frank" else float(s.get("grid_markup_eur_kwh", 0.133))
            for row in rows:
                try:
                    dt_raw = datetime.fromisoformat(row["from"])
                    if dt_raw.tzinfo is None:
                        dt_raw = dt_raw.replace(tzinfo=timezone.utc)
                    dt_loc = dt_raw.astimezone(ZoneInfo(tz_name))
                    if dt_loc.date() == now_local.date() and dt_loc.hour == now_local.hour:
                        price = float(row["marketPrice"]) + markup
                        break
                except Exception:
                    continue

    if price is None:
        log.debug("PV limiter: price=None (plan_slots=%d, price_cache_keys=%s, hour=%d)",
                  len(slots), list(_price_cache.keys()), now_local.hour)
        return

    if price < threshold:
        max_charge_kw = float(s.get("max_charge_kw", 3.0))
        max_soc_pct   = float(s.get("max_soc", 95))

        # Use live SOC from last_soc.json (updated every ~30s by data collector).
        # Fall back to plan estimate if the file is stale/missing.
        live_soc: float | None = None
        try:
            _soc_file = os.path.join(DATA_DIR, "last_soc.json")
            with open(_soc_file, encoding="utf-8") as _f:
                _sc = json.load(_f)
            if time.time() - _sc.get("ts", 0) < 300:   # accept if < 5 min old
                live_soc = float(_sc["soc"])
        except Exception:
            pass
        effective_soc = live_soc if live_soc is not None else soc_pct

        bat_full = (effective_soc is not None and effective_soc >= max_soc_pct - 2)

        action_up = (slot_action or "").upper()
        if bat_full:
            # Battery is at max SOC — solar only needs to cover house consumption.
            # Never limit below house consumption (would cause grid import).
            bat_adj_w = 0
        elif action_up in ("DISCHARGE", "FORCE_DISCHARGE"):
            # Battery discharges to house — solar covers only the remainder.
            bat_adj_w = -int(max_charge_kw * 1000)
        else:
            # Battery not full (anti-feed / solar_charge / grid_charge / neutral):
            # reserve full charging headroom so solar fills the battery.
            bat_adj_w = int(max_charge_kw * 1000)

        # Floor: always allow at least house consumption worth of solar.
        target_w = max(int(cons_w), int(cons_w + bat_adj_w + margin_w))
        target_w = max(0, min(max_w, target_w))
    else:
        target_w = max_w   # price OK → full PV output

    last_w = auto.get("pv_limiter_last_w")
    # Allow 50 W hysteresis to avoid chattering
    if last_w is not None and abs(last_w - target_w) < 50:
        return

    log.debug("PV limiter: target=%dW price=%.4f threshold=%.4f action=%s → calling HA",
              target_w, price, threshold, slot_action)
    ok = _pv_send(s, entity, use_service, svc_str, target_w)
    if ok:
        auto["pv_limiter_last_w"] = target_w
        log.info("PV limiter → %dW (price=%.4f, cons=%.0fW, bat_chg=%dW, threshold=%.4f)",
                 target_w, price, cons_w,
                 int(float(s.get("max_charge_kw", 3.0)) * 1000) if price < threshold else 0,
                 threshold)
    else:
        log.warning("PV limiter: HA service call failed (entity=%s service=%s)", entity, svc_str)


def _pv_limiter_tick() -> None:
    """Fast tick (every 15 s) that only handles the PV power limiter.
    Runs independently of the battery automation toggle."""
    s = load_strategy_settings()
    if not s.get("pv_limiter_enabled"):
        log.debug("PV limiter tick: disabled in settings")
        return
    log.debug("PV limiter tick: enabled, entity=%s use_service=%s service=%s",
              s.get("pv_limiter_entity"), s.get("pv_limiter_use_service"), s.get("pv_limiter_service"))
    auto = _load_automation()
    _apply_pv_limiter(s, auto)
    _save_automation(auto)


def _start_automation_thread(interval: int = 60) -> None:
    import threading

    def loop():
        import time as _time
        while True:
            try:
                _automation_tick()
            except Exception as exc:
                log.warning("Automation thread error: %s", exc)
            _time.sleep(interval)

    def pv_loop():
        import time as _time
        while True:
            try:
                _pv_limiter_tick()
            except Exception as exc:
                log.warning("PV limiter tick error: %s", exc)
            _time.sleep(5)

    t = threading.Thread(target=loop, daemon=True, name="automation")
    t.start()
    log.info("Automation background thread started (interval=%ds)", interval)

    pv = threading.Thread(target=pv_loop, daemon=True, name="pv_limiter")
    pv.start()
    log.info("PV limiter background thread started (interval=15s)")


@app.route("/api/automation", methods=["GET"])
def get_automation():
    data = _load_automation()
    data["current_action"] = _current_slot_action()
    return jsonify(data)


@app.route("/api/automation", methods=["POST"])
def set_automation():
    body = request.get_json(force=True) or {}
    data = _load_automation()
    if "enabled" in body:
        newly_enabled = bool(body["enabled"]) and not data.get("enabled")
        data["enabled"] = bool(body["enabled"])
        if newly_enabled:
            # Clear last_action so the tick immediately applies the current action
            data["last_action"] = None
    _save_automation(data)
    log.info("Automation %s", "ENABLED" if data["enabled"] else "DISABLED")
    data["current_action"] = _current_slot_action()
    return jsonify(data)


# ---------------------------------------------------------------------------
# Profit analysis: automation savings vs. anti-feed baseline
# ---------------------------------------------------------------------------

def _query_profit_day(date_str: str, tz_name: str) -> dict:
    """
    Query the user-configured external InfluxDB for hourly averages of
    solar_w, net_w and house_w for a specific calendar date.
    Returns {hour_int: {"solar_w": float, "net_w": float, "house_w": float}}.
    Falls back to {} when InfluxDB is not configured or unreachable.
    """
    import pytz as _pytz
    from datetime import datetime as _dt, timedelta as _td

    influx_src = _load_influx_source()
    conn       = _load_influx_conn()
    url        = influx_src.get("url") or conn.get("url", "")
    version    = influx_src.get("version") or conn.get("version", "v1")
    database   = influx_src.get("database", "")
    username   = conn.get("username", "")
    password   = conn.get("password", "")
    mappings   = influx_src.get("mappings", {})

    if not url or not database:
        log.debug("profit: external InfluxDB not configured")
        return {}

    tz_obj = _pytz.timezone(tz_name)
    _d     = _dt.strptime(date_str, "%Y-%m-%d")
    # Wide UTC window (±14 h) so we always capture the full local day
    start  = (_d - _td(hours=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end    = (_d + _td(hours=38)).strftime("%Y-%m-%dT%H:%M:%SZ")

    result: dict[int, dict] = {}

    for field_name in ("solar_w", "net_w", "house_w"):
        mapping = mappings.get(field_name)
        if not mapping:
            continue
        if isinstance(mapping, list):
            mapping = mapping[0]

        field   = mapping.get("field", "value")
        meas    = mapping.get("measurement") or influx_src.get("measurement", "")
        tag_key = mapping.get("tag_key", "")
        tag_val = mapping.get("tag_value", "")
        # "invert": true flips the sign — use when positive in InfluxDB means
        # export/teruglevering instead of the standard positive=import convention.
        sign    = -1.0 if mapping.get("invert", False) else 1.0
        if not meas:
            continue

        where_parts = [f"time >= '{start}' AND time < '{end}'"]
        if tag_key and tag_val:
            where_parts.append(f'"{tag_key}" = \'{tag_val}\'')

        q = (f'SELECT mean("{field}") AS val FROM "{meas}"'
             f' WHERE {" AND ".join(where_parts)}'
             f' GROUP BY time(1h) fill(null)')
        try:
            data = _influx_v1_query(url, username, password, q, db=database)
            for res in data.get("results", []):
                for series in res.get("series", []):
                    for row in series.get("values", []):
                        ts_raw, val = row[0], row[1]
                        if val is None:
                            continue
                        try:
                            dt_utc = _dt.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                                tzinfo=_pytz.utc)
                            dt_loc = dt_utc.astimezone(tz_obj)
                            if dt_loc.strftime("%Y-%m-%d") == date_str:
                                h = dt_loc.hour
                                result.setdefault(h, {})[field_name] = sign * float(val)
                        except Exception:
                            pass
        except Exception as exc:
            log.debug("profit: influx query %s/%s %s: %s", field_name, meas, date_str, exc)

    # If net_w not mapped but solar_w and house_w are: approximate from those two
    for h, hd in result.items():
        if "net_w" not in hd and "solar_w" in hd and "house_w" in hd:
            hd["net_w"] = hd["house_w"] - hd["solar_w"]   # positive = importing

    return result


@app.route("/api/profit")
def get_profit_analysis():
    """
    Compare estimated energy cost WITH automation vs WITHOUT automation
    (anti-feed only baseline) using historical InfluxDB measurements and
    historical electricity prices.

    Query params:
      days=30  – how many past days to analyse (max 90)
    """
    days_param = min(max(int(request.args.get("days", 30)), 1), 90)
    s          = load_strategy_settings()
    tz_name    = s.get("timezone", "Europe/Brussels")
    tz         = ZoneInfo(tz_name)
    cap_kwh    = float(s.get("bat_capacity_kwh",  5.0))
    rte        = float(s.get("rte",               0.85))
    min_res    = float(s.get("min_reserve_soc",   10))  / 100.0
    max_soc_f  = float(s.get("max_soc",           95))  / 100.0
    sell_price = float(s.get("feed_in_price_eur_kwh", 0.0))
    price_src  = s.get("price_source", "entsoe")

    bat_min_kwh = min_res  * cap_kwh
    bat_max_kwh = max_soc_f * cap_kwh

    # Determine price fetch params
    if price_src == "frank":
        markup       = 0.0
        frank_sess   = _frank_session()
        frank_token  = frank_sess.get("authToken")
        frank_country = frank_sess.get("country", "BE")
    else:
        markup  = float(s.get("grid_markup_eur_kwh", 0.12))
        es      = _entsoe_settings()
        api_key = es.get("apiKey", "")
        country = es.get("country", "BE")

    today     = datetime.now(tz).date()
    days_data = []

    # "Zonder auto" simulation: carry SOC across days so end-of-day SOC feeds
    # into the next day instead of resetting to 50% every day.
    bat_kwh = 0.5 * cap_kwh
    bat_kwh = max(bat_min_kwh, min(bat_max_kwh, bat_kwh))

    for d_offset in range(days_param, -1, -1):  # include today (offset=0)
        day      = today - timedelta(days=d_offset)
        date_str = day.isoformat()

        # ── 1. Fetch historical prices ────────────────────────────────────
        price_by_hour: dict[int, float] = {}
        try:
            if price_src == "frank" and (frank_token or frank_country == "BE"):
                rows = _fetch_prices(frank_token, day, day + timedelta(days=1), frank_country)
                for row in rows:
                    try:
                        dt_raw = datetime.fromisoformat(row["from"])
                        dt_loc = (dt_raw.replace(tzinfo=tz) if dt_raw.tzinfo is None
                                  else dt_raw.astimezone(tz))
                        if dt_loc.date() == day:
                            all_in = (
                                (row.get("marketPrice")         or 0.0) +
                                (row.get("marketPriceTax")      or 0.0) +
                                (row.get("sourcingMarkupPrice") or 0.0) +
                                (row.get("energyTaxPrice")      or 0.0)
                            )
                            price_by_hour[dt_loc.hour] = all_in
                    except Exception:
                        pass
            elif price_src == "entsoe" and api_key:
                rows = _fetch_entsoe_day(api_key, day, country, tz_name)
                for row in rows:
                    try:
                        dt_raw = datetime.fromisoformat(row["from"])
                        dt_loc = (dt_raw.replace(tzinfo=tz) if dt_raw.tzinfo is None
                                  else dt_raw.astimezone(tz))
                        if dt_loc.date() == day:
                            price_by_hour[dt_loc.hour] = float(row["marketPrice"]) + markup
                    except Exception:
                        pass
        except Exception as e:
            log.debug("profit: price fetch %s: %s", date_str, e)

        if len(price_by_hour) < 12:
            continue   # not enough price data for this day

        # ── 2. Fetch actual measurements from external InfluxDB ───────────
        actuals = _query_profit_day(date_str, tz_name)
        if not actuals or len(actuals) < 4:
            continue   # no sensor data for this day

        # ── 3. Compute costs ──────────────────────────────────────────────
        cost_with = 0.0
        cost_no   = 0.0
        # bat_kwh carries over from the previous day's simulation end-state

        hours_detail = []

        for h in range(24):
            # Price: use exact hour or nearest available
            price = price_by_hour.get(h)
            if price is None:
                candidates = sorted(price_by_hour.items(), key=lambda x: abs(x[0] - h))
                price = candidates[0][1] if candidates else 0.15

            hd = actuals.get(h, {})
            solar_wh = float(hd.get("solar_w") or 0.0)
            house_wh = float(hd.get("house_w") or 0.0)
            net_wh   = float(hd.get("net_w")   or 0.0)   # + = import, − = export

            # WITH automation: actual measured grid flow
            import_with = max(0.0, net_wh) / 1000.0
            export_with = max(0.0, -net_wh) / 1000.0
            cost_with_h = import_with * price - export_with * sell_price
            cost_with  += cost_with_h

            # WITHOUT automation: anti-feed simulation
            net = solar_wh - house_wh
            if net >= 0.0:
                headroom   = bat_max_kwh - bat_kwh
                charge     = min((net / 1000.0) * rte, headroom)
                bat_kwh    = min(bat_max_kwh, bat_kwh + charge)
                draw_for_charge = (charge / rte) if rte > 0 else 0.0
                export_no  = max(0.0, (net / 1000.0) - draw_for_charge)
                import_no  = 0.0
            else:
                deficit    = (-net) / 1000.0
                avail      = max(0.0, bat_kwh - bat_min_kwh)
                discharge  = min(deficit, avail)
                bat_kwh    = max(bat_min_kwh, bat_kwh - discharge)
                import_no  = max(0.0, deficit - discharge)
                export_no  = 0.0

            cost_no_h = import_no * price - export_no * sell_price
            cost_no  += cost_no_h

            hours_detail.append({
                "h":            h,
                "price_ct":     round(price * 100, 2),
                "solar_wh":     round(solar_wh, 0),
                "house_wh":     round(house_wh, 0),
                "net_wh":       round(net_wh, 0),
                "cost_no_ct":   round(cost_no_h * 100, 1),
                "cost_with_ct": round(cost_with_h * 100, 1),
            })

        days_data.append({
            "date":         date_str,
            "cost_no_eur":  round(cost_no,             3),
            "cost_with_eur": round(cost_with,           3),
            "savings_eur":  round(cost_no - cost_with,  3),
            "hours":        hours_detail,
        })

    if not days_data:
        return jsonify({
            "days":    [],
            "summary": None,
            "warning": (
                "Geen data beschikbaar. Controleer: (1) InfluxDB URL + database in Instellingen, "
                "(2) InfluxDB slotmapping voor solar_w / net_w / house_w, "
                "(3) prijsbron (ENTSO-E API-sleutel of Frank Energie login)."
            ),
        })

    total_sav  = sum(d["savings_eur"]  for d in days_data)
    total_no   = sum(d["cost_no_eur"]  for d in days_data)
    total_with = sum(d["cost_with_eur"] for d in days_data)
    n          = len(days_data)

    return jsonify({
        "days": days_data,
        "summary": {
            "total_savings_eur":    round(total_sav,  2),
            "total_cost_no_eur":    round(total_no,   2),
            "total_cost_with_eur":  round(total_with, 2),
            "days_with_data":       n,
            "avg_daily_savings_eur": round(total_sav / n, 3),
            "pct_saved":            round((1 - total_with / total_no) * 100, 1) if total_no > 0 else 0,
        },
    })


def _prefetch_prices() -> None:
    """Populate _price_cache at startup so the PV limiter works immediately."""
    import threading, time as _time
    def _do():
        _time.sleep(3)   # give Flask a moment to finish binding
        try:
            today    = date.today()
            tomorrow = today + timedelta(days=1)
            session      = _frank_session()
            auth_token   = session.get("authToken")
            country      = session.get("country") or _country_from_token(auth_token or "") or "NL"
            today_prices = _fetch_prices(auth_token, today, tomorrow, country)
            tomorrow_prices: list = []
            try:
                tomorrow_prices = _fetch_prices(auth_token, tomorrow, tomorrow + timedelta(days=1), country)
            except Exception:
                pass
            result = {
                "today":    today_prices,
                "tomorrow": tomorrow_prices,
                "loggedIn": bool(auth_token),
                "email":    session.get("email"),
            }
            _price_cache[today.isoformat()] = {"data": result, "ts": _time.time()}
            log.info("Startup price prefetch OK  today=%d slots  tomorrow=%d slots",
                     len(today_prices), len(tomorrow_prices))
        except Exception as exc:
            log.warning("Startup price prefetch failed: %s", exc)
    threading.Thread(target=_do, daemon=True, name="price_prefetch").start()


if __name__ == "__main__":
    # Restore persisted caches so external APIs are not hammered after restarts
    _restore_plan_cache()
    _load_forecast_disk_cache()
    # Pre-fetch prices so PV limiter works immediately after restart
    _prefetch_prices()
    # Start InfluxDB background writer
    start_background_writer(_influx_context, interval=30)
    # Start automation background thread
    _start_automation_thread(interval=60)
    print("Marstek Dashboard → http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
