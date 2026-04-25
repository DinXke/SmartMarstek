"""
sma_modbus.py – SMA Sunny Boy Modbus TCP reader.

Polls SMA inverter input registers (FC04) periodically and exposes
the latest reading via get_sma_live(). Starts a background thread
via start_sma_reader(get_settings_fn).

SMA register addressing:
  - 3xxxx registers → input registers (FC04), 0-based addr = reg - 1
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
    "ts":          0.0,
    "pac_w":       None,
    "e_day_wh":    None,
    "e_total_wh":  None,
    "grid_v":      None,
    "freq_hz":     None,
    "temp_c":      None,
    "op_time_s":   None,
    "online":      False,
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
            address=address, count=count, slave=unit_id
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
            address=address, count=count, slave=unit_id
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
    Extended diagnostic poll: tries multiple register ranges and function codes.
    Returns raw register values to help debug register map issues.
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

    # Register map validated against Loxone config for SMA Sunny Boy AV 4.0
    # (reg, count, type, scale, fc)  — addr = reg - 1 (0-based)
    PROBES_FC03 = [  # holding registers
        ("pac_w",      30774, 2, "S32",   1),   # reg 30775 — Pac
        ("grid_v",     30782, 2, "U32", 100),   # reg 30783 — Uac L1 (0.01 V)
        ("temp_c",     30952, 2, "S32",  10),   # reg 30953 — Internal temp (0.1°C)
        ("wmax_lim_w", 42061, 2, "U32",   1),   # reg 42062 — WMaxLim (PV limiter)
        ("wmax_lim_pct",40235,2, "U32",   1),   # reg 40236 — WMaxLimPct
    ]
    PROBES_FC04 = [  # input registers
        ("e_day_wh",   30534, 2, "U32",   1),   # reg 30535 — E-Day (Wh)
        ("e_total_wh", 30530, 2, "U32",   1),   # reg 30531 — E-Total (Wh)
        ("freq_hz",    30802, 2, "U32", 100),   # reg 30803 — Grid freq (0.01 Hz)
        ("op_time_s",  30540, 2, "U32",   1),   # reg 30541 — Operating time (s)
        ("insulation", 30224, 2, "U32",   1),   # reg 30225 — Insulation resistance
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
        val = round(parsed / scale, 2) if parsed is not None and scale > 1 else parsed
        is_nan = parsed is None
        result["raw"][key] = {"fc": fc, "addr": addr + 1, "regs": raw_hex, "value": val, "nan": is_nan}
        if is_nan:
            nan_count += 1
        elif val is not None:
            val_count += 1
            result[key] = val
        return val

    try:
        for key, addr, cnt, dtype, scale in PROBES_FC03:
            _probe(key, addr, cnt, dtype, scale, 3)
        for key, addr, cnt, dtype, scale in PROBES_FC04:
            _probe(key, addr, cnt, dtype, scale, 4)

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

    SMA documents 1-based addresses; pymodbus uses 0-based → subtract 1.
    """
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        log.error("pymodbus niet geïnstalleerd")
        return {"online": False}

    # Register map validated against Loxone config for SMA Sunny Boy AV 4.0
    # FC03 = holding registers, FC04 = input registers
    # Addresses are 0-based (SMA 1-based reg - 1)
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

        # E-Day — reg 30535, FC04, U32, Wh
        r = _read_input(client, 30534, 2, unit_id)
        if r is not None:
            data["e_day_wh"] = _to_u32(r, 0)

        # E-Total — reg 30531, FC04, U32, Wh
        r = _read_input(client, 30530, 2, unit_id)
        if r is not None:
            data["e_total_wh"] = _to_u32(r, 0)

        # Grid voltage L1 — reg 30783, FC03, U32, 0.01 V
        r = _read_holding(client, 30782, 2, unit_id)
        if r is not None:
            v = _to_u32(r, 0)
            data["grid_v"] = round(v / 100, 2) if v is not None else None

        # Grid frequency — reg 30803, FC04, U32, 0.01 Hz
        r = _read_input(client, 30802, 2, unit_id)
        if r is not None:
            f = _to_u32(r, 0)
            data["freq_hz"] = round(f / 100, 2) if f is not None else None

        # Internal temperature — reg 30953, FC03, S32, 0.1 °C
        r = _read_holding(client, 30952, 2, unit_id)
        if r is not None:
            t = _to_s32(r, 0)
            data["temp_c"] = round(t / 10, 1) if t is not None else None

        # Operating time — reg 30541, FC04, U32, s
        r = _read_input(client, 30540, 2, unit_id)
        if r is not None:
            data["op_time_s"] = _to_u32(r, 0)

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
    "offline_since":    None,   # float timestamp when online→offline transition happened
    "offline_notified": False,  # True = already sent the offline alert this outage
    "last_error_code":  None,   # last status_code that was an error
    "day_summary_sent_date": None,  # "YYYY-MM-DD" of last day-summary
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
    if online and code is not None and code == 1392:  # 1392 = Fout
        if _alert_state["last_error_code"] != code:
            _notify("sma_error", {
                "message": f"SMA foutcode: {code} ({result.get('status', '?')})",
            })
            _alert_state["last_error_code"] = code
    elif code != 1392:
        _alert_state["last_error_code"] = None

    # ── Day summary (sent once per day around sunset: pac drops to 0) ────────
    if online:
        pac = result.get("pac_w") or 0
        today = str(_date.today())
        if pac == 0 and _alert_state["day_summary_sent_date"] != today:
            e_day = result.get("e_day_wh")
            if e_day is not None and e_day > 100:  # only if we actually produced something
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

# Well-known SMA register labels (1-based) for annotation in scan results
_KNOWN_REGS: dict[int, str] = {
    30775: "Pac (AC vermogen, W)",
    30531: "E-Total (totaalopbrengst, Wh)",
    30535: "E-Day (dagopbrengst, Wh)",
    30783: "Uac L1 (netspanning, 0.01V)",
    30803: "Freq / status",
    30541: "Bedrijfstijd (s)",
    30953: "Interne temperatuur (0.1°C)",
    30225: "Isolatieweerstand",
    30201: "Apparaatstatus",
    30233: "Foutcode",
    30517: "E-Total alternatief (Wh)",
    30529: "DC-vermogen string 1 (W)",
    30533: "DC-spanning string 1 (0.01V)",
    30581: "Uac L1 alternatief (0.01V)",
    30977: "Frequentie alternatief (0.01Hz)",
    40185: "Max schijnbaar vermogen (VA)",
    40195: "Schijnbaar vermogensgrens (VA)",
    40210: "Modus netinvoerbeheer",
    40212: "Actief vermogenslimiet P",
    40214: "Actief vermogenslimiet config",
    40236: "WMaxLimPct (%)",
    40915: "Ingestelde vermogensgrens",
    42062: "WMaxLim — PV limiter (W)",
}

# SMA register ranges worth scanning (start_addr_0based, count, fc)
_SCAN_RANGES = [
    # FC04 input registers — measurement data
    (30000, 1000, 4),   # 30001–31000: main measurement block
    # FC03 holding registers — measurements + control
    (30000, 1000, 3),   # 30001–31000 via FC03
    (40000,  500, 3),   # 40001–40500: control registers
    (40900,  200, 3),   # 40901–41100: extended control
    (42000,  100, 3),   # 42001–42100: WMaxLim area
]

_BLOCK = 10  # registers per read attempt


def scan_registers(host: str, port: int, unit_id: int,
                   progress_cb=None) -> list[dict]:
    """
    Scan all SMA register ranges via FC03 and FC04.
    Returns a list of dicts for every register address that returned a
    non-NaN, non-error value.

    progress_cb(done, total) is called after each block if provided.
    This call is synchronous and may take 30–90 seconds depending on the
    inverter's response speed.
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
                            address=addr, count=batch, slave=unit_id
                        )
                    else:
                        result = client.read_input_registers(
                            address=addr, count=batch, slave=unit_id
                        )
                    if not (hasattr(result, "isError") and result.isError()):
                        regs = result.registers
                        for i, raw in enumerate(regs):
                            reg_1based = addr + i + 1  # back to 1-based for display
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
