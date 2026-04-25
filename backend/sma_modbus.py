"""
sma_modbus.py – SMA Sunny Boy Modbus TCP reader.

Polls SMA inverter registers periodically and exposes the latest reading
via get_sma_live(). Starts a background thread via start_sma_reader().

SMA register addressing (SB30-50-1AV-40 / SBn-n-1AV-40):
  - 3xxxx registers → FC04 input registers OR FC03 holding registers
  - pymodbus uses 0-based addressing → 1-based register number - 1
  - NaN sentinels: U32=0xFFFFFFFF, S32=0x80000000, U64=0x8000000000000000
"""

import logging
import struct
import threading
import time
from typing import Optional

log = logging.getLogger("sma_modbus")

# ---------------------------------------------------------------------------
# SMA status codes → human-readable label
# ---------------------------------------------------------------------------

_STATUS_LABELS: dict[int, str] = {
    303:  "Uit",
    307:  "Netinvoer",
    308:  "Wacht op net",
    381:  "Stop",
    455:  "Vermogen beperkt",
    1392: "Fout",
}

_SMA_U32_NAN: int = 0xFFFF_FFFF
_SMA_S32_NAN: int = 0x8000_0000
_SMA_U64_NAN: int = 0x8000_0000_0000_0000

# ---------------------------------------------------------------------------
# In-memory cache  (ts=0.0 → never polled)
# ---------------------------------------------------------------------------

_sma_live: dict = {
    "ts":           0.0,
    "pac_w":        None,
    "e_day_wh":     None,
    "e_total_wh":   None,
    "grid_v":       None,
    "freq_hz":      None,
    "temp_c":       None,
    "op_time_s":    None,
    "dc_power_w":   None,
    "dc_voltage_v": None,
    "dc_current_a": None,
    "status":       None,
    "status_code":  None,
    "online":       False,
}
_sma_lock = threading.Lock()


def get_sma_live() -> dict:
    """Return a copy of the latest SMA live data."""
    with _sma_lock:
        return dict(_sma_live)


# ---------------------------------------------------------------------------
# Register decode helpers
# ---------------------------------------------------------------------------

def _to_u32(regs: list, idx: int) -> Optional[int]:
    if len(regs) < idx + 2:
        return None
    val = (regs[idx] << 16) | regs[idx + 1]
    return None if val == _SMA_U32_NAN else val


def _to_s32(regs: list, idx: int) -> Optional[int]:
    if len(regs) < idx + 2:
        return None
    raw = (regs[idx] << 16) | regs[idx + 1]
    if raw == _SMA_S32_NAN:
        return None
    return struct.unpack(">i", struct.pack(">I", raw))[0]


def _to_u64(regs: list, idx: int) -> Optional[int]:
    if len(regs) < idx + 4:
        return None
    val = (
        (regs[idx]     << 48)
        | (regs[idx+1] << 32)
        | (regs[idx+2] << 16)
        | regs[idx+3]
    )
    return None if val == _SMA_U64_NAN else val


def _read_input(client, address: int, count: int, unit_id: int) -> Optional[list]:
    """Read `count` input registers (FC04) starting at 0-based `address`."""
    try:
        result = client.read_input_registers(
            address=address, count=count, device_id=unit_id
        )
        if hasattr(result, "isError") and result.isError():
            log.debug("SMA FC04 error  addr=%d  unit=%d: %s", address, unit_id, result)
            return None
        return result.registers
    except Exception as exc:
        log.debug("SMA FC04 exception  addr=%d: %s", address, exc)
        return None


def _read_holding(client, address: int, count: int, unit_id: int) -> Optional[list]:
    """Read `count` holding registers (FC03) starting at 0-based `address`."""
    try:
        result = client.read_holding_registers(
            address=address, count=count, device_id=unit_id
        )
        if hasattr(result, "isError") and result.isError():
            log.debug("SMA FC03 error  addr=%d  unit=%d: %s", address, unit_id, result)
            return None
        return result.registers
    except Exception as exc:
        log.debug("SMA FC03 exception  addr=%d: %s", address, exc)
        return None


def poll_diagnostics(host: str, port: int, unit_id: int) -> dict:
    """
    Extended diagnostic poll: tries all key registers and returns raw values.
    Used by /api/sma/test.
    """
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        return {"online": False, "error": "pymodbus niet geïnstalleerd"}

    client = ModbusTcpClient(host=host, port=port, timeout=5)
    if not client.connect():
        return {"online": False, "error": f"Kan niet verbinden met {host}:{port}"}

    result: dict = {"online": True, "raw": {}, "unit_id": unit_id}

    # (key, 0-based addr, count, dtype, scale, fc)
    # All from official SMA SB30-50-1AV-40 Modbus register map
    PROBES_FC03 = [
        ("pac_w",        30774, 2, "S32",    1, 3),  # reg 30775 — Pac (W)
        ("grid_v",       30782, 2, "U32",  100, 3),  # reg 30783 — Uac L1 (0.01V)
        ("temp_c",       30952, 2, "S32",   10, 3),  # reg 30953 — Internal temp (0.1°C)
        ("status_code",  30200, 2, "U32",    1, 3),  # reg 30201 — Device status (ENUM)
        ("wmax_lim_w",   42061, 2, "U32",    1, 3),  # reg 42062 — WMaxLim (PV limiter)
        ("wmax_lim_pct", 40235, 2, "U32",    1, 3),  # reg 40236 — WMaxLimPct
    ]
    PROBES_FC04 = [
        ("e_total_wh",   30512, 4, "U64",    1, 4),  # reg 30513 — E-Total (Wh) U64
        ("e_day_wh",     30516, 4, "U64",    1, 4),  # reg 30517 — E-Day (Wh) U64
        ("freq_hz",      30802, 2, "U32",  100, 4),  # reg 30803 — Grid freq (0.01Hz)
        ("op_time_s",    30540, 2, "U32",    1, 4),  # reg 30541 — Operating time (s)
        ("dc_current_a", 30768, 2, "U32", 1000, 4),  # reg 30769 — DC current str1 (mA)
        ("dc_voltage_v", 30770, 2, "U32",  100, 4),  # reg 30771 — DC voltage str1 (0.01V)
        ("dc_power_w",   30772, 2, "S32",    1, 4),  # reg 30773 — DC power str1 (W)
    ]

    nan_count = 0
    val_count = 0

    def _probe(key, addr, cnt, dtype, scale, fc):
        nonlocal nan_count, val_count
        regs = _read_holding(client, addr, cnt, unit_id) if fc == 3 else _read_input(client, addr, cnt, unit_id)
        if regs is None:
            result["raw"][key] = {"fc": fc, "addr": addr + 1, "status": "read_error"}
            return None
        raw_hex = [f"0x{r:04X}" for r in regs]
        if dtype == "U32":
            parsed = _to_u32(regs, 0)
        elif dtype == "S32":
            parsed = _to_s32(regs, 0)
        else:
            parsed = _to_u64(regs, 0)
        val = round(parsed / scale, 3) if parsed is not None and scale > 1 else parsed
        is_nan = parsed is None
        result["raw"][key] = {"fc": fc, "addr": addr + 1, "regs": raw_hex, "value": val, "nan": is_nan}
        if is_nan:
            nan_count += 1
        elif val is not None:
            val_count += 1
            result[key] = val
        return val

    try:
        for key, addr, cnt, dtype, scale, fc in PROBES_FC03:
            _probe(key, addr, cnt, dtype, scale, fc)
        for key, addr, cnt, dtype, scale, fc in PROBES_FC04:
            _probe(key, addr, cnt, dtype, scale, fc)

        # Decode status code to label
        if "status_code" in result:
            result["status"] = _STATUS_LABELS.get(int(result["status_code"]), f"Code {result['status_code']}")

        # Night mode: connection OK but all measurement registers returned NaN
        if nan_count > 0 and val_count == 0:
            result["night_mode"] = True
            result["night_mode_msg"] = (
                "Omvormer bereikbaar maar alle meetregisters bevatten NaN. "
                "Dit is normaal gedrag van SMA-omvormers in nacht/standby-modus. "
                "Overdag worden hier live waarden getoond."
            )

    finally:
        client.close()

    return result


# ---------------------------------------------------------------------------
# Main poll
# ---------------------------------------------------------------------------

def _poll(host: str, port: int, unit_id: int) -> dict:
    """
    Open a Modbus TCP connection to the SMA inverter, read all registers,
    and return a parsed dict. The connection is closed before returning.

    Register map: SMA SB30-50-1AV-40 (SBn-n-1AV-40) official Modbus document.
    pymodbus uses 0-based addresses → 1-based register number - 1.
    """
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        log.error("pymodbus niet geïnstalleerd")
        return {"online": False}

    client = ModbusTcpClient(host=host, port=port, timeout=5)
    if not client.connect():
        log.warning("SMA Modbus: kan niet verbinden met %s:%d", host, port)
        return {"online": False}

    data: dict = {"online": True}
    try:
        # Pac — reg 30775, FC03, S32, W
        r = _read_holding(client, 30774, 2, unit_id)
        if r is not None:
            data["pac_w"] = _to_s32(r, 0)

        # E-Total — reg 30513, FC04, U64, Wh
        r = _read_input(client, 30512, 4, unit_id)
        if r is not None:
            data["e_total_wh"] = _to_u64(r, 0)

        # E-Day — reg 30517, FC04, U64, Wh
        r = _read_input(client, 30516, 4, unit_id)
        if r is not None:
            data["e_day_wh"] = _to_u64(r, 0)

        # Grid voltage L1 — reg 30783, FC03, U32, 0.01V
        r = _read_holding(client, 30782, 2, unit_id)
        if r is not None:
            v = _to_u32(r, 0)
            data["grid_v"] = round(v / 100, 2) if v is not None else None

        # Grid frequency — reg 30803, FC04, U32, 0.01Hz
        r = _read_input(client, 30802, 2, unit_id)
        if r is not None:
            f = _to_u32(r, 0)
            data["freq_hz"] = round(f / 100, 2) if f is not None else None

        # Internal temperature — reg 30953, FC03, S32, 0.1°C
        r = _read_holding(client, 30952, 2, unit_id)
        if r is not None:
            t = _to_s32(r, 0)
            data["temp_c"] = round(t / 10, 1) if t is not None else None

        # Operating time — reg 30541, FC04, U32, s
        r = _read_input(client, 30540, 2, unit_id)
        if r is not None:
            data["op_time_s"] = _to_u32(r, 0)

        # DC current string 1 — reg 30769, FC04, U32, mA → A
        r = _read_input(client, 30768, 2, unit_id)
        if r is not None:
            c = _to_u32(r, 0)
            data["dc_current_a"] = round(c / 1000, 3) if c is not None else None

        # DC voltage string 1 — reg 30771, FC04, U32, 0.01V
        r = _read_input(client, 30770, 2, unit_id)
        if r is not None:
            v = _to_u32(r, 0)
            data["dc_voltage_v"] = round(v / 100, 2) if v is not None else None

        # DC power string 1 — reg 30773, FC04, S32, W
        r = _read_input(client, 30772, 2, unit_id)
        if r is not None:
            data["dc_power_w"] = _to_s32(r, 0)

        # Device status — reg 30201, FC03, U32 (ENUM)
        r = _read_holding(client, 30200, 2, unit_id)
        if r is not None:
            code = _to_u32(r, 0)
            if code is not None:
                data["status_code"] = code
                data["status"] = _STATUS_LABELS.get(code, f"Code {code}")

    finally:
        client.close()

    return data


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _update_cache(d: dict) -> None:
    with _sma_lock:
        _sma_live.update(d)
        _sma_live["ts"] = time.time()


_alert_state: dict = {
    "offline_since":    None,
    "offline_notified": False,
    "last_error_code":  None,
    "day_summary_sent_date": None,
}


def _check_alerts(result: dict) -> None:
    """Fire Telegram alerts based on state transitions."""
    try:
        from telegram import notify_event as _notify
        from datetime import date as _date
    except ImportError:
        return

    online = result.get("online", False)

    # ── Offline alert ────────────────────────────────────────────────────────
    if not online:
        if _alert_state["offline_since"] is None:
            _alert_state["offline_since"] = time.time()
            _alert_state["offline_notified"] = False
        elif not _alert_state["offline_notified"]:
            offline_s = time.time() - _alert_state["offline_since"]
            if offline_s >= 300:  # 5 min grace period
                _notify("sma_offline", {"message": "SMA Sunny Boy niet bereikbaar (> 5 min)"})
                _alert_state["offline_notified"] = True
    else:
        _alert_state["offline_since"] = None
        _alert_state["offline_notified"] = False

    # ── Error status alert ───────────────────────────────────────────────────
    code = result.get("status_code")
    if online and code is not None and code == 1392:
        if _alert_state["last_error_code"] != code:
            _notify("sma_error", {
                "message": f"SMA foutcode: {code} ({result.get('status', '?')})",
            })
            _alert_state["last_error_code"] = code
    elif code != 1392:
        _alert_state["last_error_code"] = None

    # ── Day summary (sent once per day when pac drops to 0) ──────────────────
    if online:
        pac = result.get("pac_w") or 0
        today = str(_date.today())
        if pac == 0 and _alert_state["day_summary_sent_date"] != today:
            e_day = result.get("e_day_wh")
            if e_day is not None and e_day > 100:
                kwh = round(e_day / 1000, 2)
                _notify("sma_day_summary", {
                    "message": f"SMA dagopbrengst: {kwh} kWh",
                    "e_day_wh": e_day,
                })
                _alert_state["day_summary_sent_date"] = today


def _reader_loop(get_settings_fn, interval: int) -> None:
    log.info("SMA Modbus reader gestart  interval=%ds", interval)
    while True:
        try:
            s = get_settings_fn()
            if not s.get("sma_reader_enabled"):
                time.sleep(interval)
                continue
            host    = (s.get("sma_reader_host") or "").strip()
            port    = int(s.get("sma_reader_port", 502))
            unit_id = int(s.get("sma_reader_unit_id", 3))
            if not host:
                time.sleep(interval)
                continue
            result = _poll(host, port, unit_id)
            _update_cache(result)
            _check_alerts(result)
            log.debug(
                "SMA live  pac=%sW  e_day=%sWh  status=%s",
                result.get("pac_w"), result.get("e_day_wh"), result.get("status"),
            )
        except Exception as exc:
            log.warning("SMA reader loop uitzondering: %s", exc)
            with _sma_lock:
                _sma_live["online"] = False
                _sma_live["ts"] = time.time()
        time.sleep(interval)


def start_sma_reader(get_settings_fn, interval: int = 10) -> threading.Thread:
    """Spawn a background daemon thread that polls the SMA inverter."""
    t = threading.Thread(
        target=_reader_loop,
        args=(get_settings_fn, interval),
        daemon=True,
        name="sma-reader",
    )
    t.start()
    return t


# ---------------------------------------------------------------------------
# Modbus register scanner
# ---------------------------------------------------------------------------

# Well-known SMA register labels (1-based) — SB30-50-1AV-40 register map
_KNOWN_REGS: dict[int, str] = {
    30201: "Apparaatstatus (ENUM)",
    30233: "Foutcode (ENUM)",
    30513: "E-Total — totaalopbrengst (Wh, U64)",
    30517: "E-Day — dagopbrengst (Wh, U64)",
    30521: "E-Total alternatief (Wh, U64)",
    30535: "E-Day alternatief (Wh, U32)",
    30541: "Bedrijfstijd (s, U32)",
    30769: "DC stroom string 1 (mA, U32)",
    30771: "DC spanning string 1 (0.01V, U32)",
    30773: "DC vermogen string 1 (W, S32)",
    30775: "Pac — AC vermogen (W, S32)",
    30783: "Uac L1 — netspanning (0.01V, U32)",
    30803: "Netfrequentie (0.01Hz, U32)",
    30813: "Uac L2 — netspanning fase 2 (0.01V, U32)",
    30823: "Uac L3 — netspanning fase 3 (0.01V, U32)",
    30953: "Interne temperatuur (0.1°C, S32)",
    40185: "Max schijnbaar vermogen (VA)",
    40236: "WMaxLimPct — vermogenslimiet (%)",
    42062: "WMaxLim — PV limiter absolute (W)",
}

# SMA register ranges worth scanning (start_addr_0based, count, fc)
_SCAN_RANGES = [
    (30000, 1000, 4),   # FC04: 30001–31000 — main measurement block
    (30000, 1000, 3),   # FC03: 30001–31000 — same range via holding
    (40000,  500, 3),   # FC03: 40001–40500 — control registers
    (40900,  200, 3),   # FC03: 40901–41100 — extended control
    (42000,  100, 3),   # FC03: 42001–42100 — WMaxLim area
]

_BLOCK = 10  # registers per read attempt


def scan_registers(host: str, port: int, unit_id: int,
                   progress_cb=None) -> list[dict]:
    """
    Scan all SMA register ranges via FC03 and FC04.
    Returns a list of dicts for every register address that returned a
    non-NaN, non-error value.

    progress_cb(done, total) is called after each block if provided.
    This call is synchronous and may take 30–90 seconds.
    """
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        return []

    client = ModbusTcpClient(host=host, port=port, timeout=3)
    if not client.connect():
        return []

    found: list[dict] = []
    total_blocks = sum((count // _BLOCK + 1) for _, count, _ in _SCAN_RANGES)
    done_blocks  = 0

    try:
        for range_start, range_count, fc in _SCAN_RANGES:
            addr = range_start
            end  = range_start + range_count
            while addr < end:
                batch = min(_BLOCK, end - addr)
                try:
                    if fc == 3:
                        result = client.read_holding_registers(
                            address=addr, count=batch, device_id=unit_id
                        )
                    else:
                        result = client.read_input_registers(
                            address=addr, count=batch, device_id=unit_id
                        )
                    if not (hasattr(result, "isError") and result.isError()):
                        regs = result.registers
                        for i, raw in enumerate(regs):
                            reg_1based = addr + i + 1
                            if raw in (0xFFFF, 0x8000, 0xFFFFFFFF, 0x80000000):
                                continue  # NaN sentinel
                            found.append({
                                "reg":   reg_1based,
                                "addr":  addr + i,
                                "fc":    fc,
                                "raw":   raw,
                                "hex":   f"0x{raw:04X}",
                                "label": _KNOWN_REGS.get(reg_1based, ""),
                            })
                except Exception:
                    pass
                addr += batch
                done_blocks += 1
                if progress_cb:
                    progress_cb(done_blocks, total_blocks)
    finally:
        client.close()

    # Deduplicate: keep first occurrence per (reg, fc)
    seen: set = set()
    unique: list[dict] = []
    for item in found:
        key = (item["reg"], item["fc"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return sorted(unique, key=lambda x: (x["reg"], x["fc"]))
