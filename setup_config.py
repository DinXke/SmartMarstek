#!/usr/bin/env python3
"""
setup_config.py  –  runs at add-on startup before Flask starts.
Reads /data/options.json (written by HA Supervisor from the add-on config tab)
and writes the individual settings JSON files that the Flask app reads.

Only writes a file when the relevant option is non-empty, so users can still
manage settings via the SmartMarstek web UI when they leave a field blank.
"""
import json
import os
import sys

DATA_DIR     = os.environ.get("MARSTEK_DATA_DIR", "/data")
OPTIONS_FILE = "/data/options.json"


def load_options() -> dict:
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("[setup_config] No options.json found – skipping", flush=True)
        return {}
    except Exception as exc:
        print(f"[setup_config] Could not read options.json: {exc}", flush=True)
        return {}


def write_if_changed(path: str, data: dict) -> None:
    """Write JSON only when content actually changed (avoids unnecessary disk writes)."""
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
        if existing == data:
            return
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[setup_config] Written: {path}", flush=True)


def main():
    opts = load_options()
    if not opts:
        return

    # ── Home Assistant ────────────────────────────────────────────────────
    ha_url   = (opts.get("ha_url")   or "").strip()
    ha_token = (opts.get("ha_token") or "").strip()
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "").strip()

    if ha_url and ha_token:
        # Explicitly configured by user: use as-is
        write_if_changed(
            os.path.join(DATA_DIR, "ha_settings.json"),
            {"url": ha_url, "token": ha_token},
        )
    elif supervisor_token:
        # Running as HA add-on: auto-configure only if no existing user-set URL.
        # Never overwrite a URL that was explicitly set by the user via the web UI.
        _ha_file = os.path.join(DATA_DIR, "ha_settings.json")
        _existing_url = ""
        try:
            with open(_ha_file, encoding="utf-8") as _f:
                _existing_url = json.load(_f).get("url", "")
        except Exception:
            pass
        _is_default = (not _existing_url
                       or "localhost" in _existing_url
                       or "127.0.0.1" in _existing_url)
        if _is_default:
            write_if_changed(
                _ha_file,
                {"url": "http://homeassistant:8123", "token": supervisor_token},
            )
            print("[setup_config] HA: auto-configured via Supervisor token", flush=True)
        else:
            print(f"[setup_config] HA: keeping user URL {_existing_url!r}", flush=True)
    elif ha_url or ha_token:
        print("[setup_config] HA: both ha_url and ha_token are required – skipping", flush=True)

    # ── ENTSO-E prices ────────────────────────────────────────────────────
    entsoe_key = (opts.get("entsoe_api_key") or "").strip()
    if entsoe_key:
        write_if_changed(
            os.path.join(DATA_DIR, "entsoe_settings.json"),
            {
                "apiKey":   entsoe_key,
                "country":  (opts.get("entsoe_country") or "BE").strip(),
                "timezone": (opts.get("timezone")        or "Europe/Brussels").strip(),
            },
        )

    # ── InfluxDB connection ───────────────────────────────────────────────
    # Option 1: auto-discovered HA InfluxDB add-on (run.sh sets INFLUX_ADDON_*)
    addon_host = os.environ.get("INFLUX_ADDON_HOST", "").strip()
    addon_port = os.environ.get("INFLUX_ADDON_PORT", "8086").strip()
    addon_ssl  = os.environ.get("INFLUX_ADDON_SSL",  "false").strip().lower() == "true"
    if addon_host:
        scheme = "https" if addon_ssl else "http"
        write_if_changed(
            os.path.join(DATA_DIR, "influx_connection.json"),
            {
                "url":      f"{scheme}://{addon_host}:{addon_port}",
                "version":  (opts.get("influx_version")  or "v1").strip(),
                "username": os.environ.get("INFLUX_ADDON_USERNAME", "").strip(),
                "password": os.environ.get("INFLUX_ADDON_PASSWORD", "").strip(),
            },
        )
    # Option 2: manually entered URL
    else:
        influx_url = (opts.get("influx_url") or "").strip()
        if influx_url:
            write_if_changed(
                os.path.join(DATA_DIR, "influx_connection.json"),
                {
                    "url":      influx_url,
                    "version":  (opts.get("influx_version")  or "v1").strip(),
                    "username": (opts.get("influx_username") or "").strip(),
                    "password": (opts.get("influx_password") or "").strip(),
                },
            )

    print("[setup_config] Done", flush=True)


if __name__ == "__main__":
    main()
