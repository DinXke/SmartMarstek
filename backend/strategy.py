"""
strategy.py – Battery charging / saving / discharging strategy algorithm.

Given:
  - Hourly energy prices for today + tomorrow (€/kWh)
  - Hourly solar forecast (Wh)
  - Hourly expected consumption (Wh, from InfluxDB history or manual)
  - Battery parameters (capacity, RTE, depreciation, current SOC, min reserve)

Returns a 48-slot timeline with a recommended action for each hour:

  SOLAR_CHARGE  – solar production expected > consumption; charge from solar
  GRID_CHARGE   – buy cheap grid electricity to charge battery
  SAVE          – battery has charge, hold it for upcoming expensive period
  DISCHARGE     – use battery, avoid expensive grid draw
  NEUTRAL       – do nothing special

Each slot also contains expected SOC at start/end of that hour.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("strategy")

# ---------------------------------------------------------------------------
# Settings file
# ---------------------------------------------------------------------------

_DATA_DIR = os.environ.get("MARSTEK_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
STRATEGY_SETTINGS_FILE = os.path.join(_DATA_DIR, "strategy_settings.json")

DEFAULT_SETTINGS = {
    "bat_capacity_kwh":     10.0,   # total usable battery capacity
    "rte":                  0.85,   # round-trip efficiency
    "depreciation_eur_kwh": 0.06,   # cost per kWh cycled through battery
    "min_reserve_soc":      10,     # % always kept as reserve
    "max_soc":              95,     # % max charge target
    "max_charge_kw":        3.0,    # max grid charge rate
    "sell_back":            False,  # can we sell excess to grid?
    "timezone":             "Europe/Brussels",
    # Manual peak hours override (list of hour ints 0-23).
    # Empty = derive from consumption history.
    "manual_peak_hours":    [],
    # How many consecutive hours of history to use for consumption baseline
    "history_days":         21,
    # Tax / distribution markup on top of market price (€/kWh)
    "grid_markup_eur_kwh":  0.12,
    # Price source for strategy: "entsoe" or "frank"
    # When "frank": uses Frank Energie all-in prices (incl. taxes/markup).
    #   Set grid_markup_eur_kwh to only network/distribution fee (~0.05–0.07).
    # When "entsoe": uses ENTSO-E wholesale prices + grid_markup_eur_kwh.
    "price_source":         "entsoe",
    # Consumption profile source: "auto" | "local_influx" | "external_influx" | "ha_history"
    # "auto": tries external_influx → local_influx → ha_history (fallback chain).
    "consumption_source":   "auto",
    # Standby/parasitic consumption in Watt (always-on appliances, fridges, …).
    # 0 = auto-detect from 02:00–06:00 historical average.
    # Used to filter standby-only hours out of peak detection.
    "standby_w":            0,
    # Minimum price premium for "save for better hour" to trigger.
    # 0.30 = best upcoming price must be ≥30% above current price (AND above p75).
    # Lower = more aggressive saving; higher = only save for very large spreads.
    "save_price_factor":    0.30,
    # Minimum net spread (€/kWh) between effective charge cost and best future
    # price to trigger grid charging.  5ct = charge from grid when you gain ≥5ct
    # per stored kWh after efficiency + depreciation losses.
    "min_charge_spread_eur_kwh": 0.05,
    # PV power limiter (e.g. SMA Sunny Boy via Home Assistant number entity)
    "pv_limiter_enabled":        False,
    "pv_limiter_entity":         "",     # HA entity_id for number.set_value mode
    "pv_limiter_max_w":          4000,   # restore to this value (W) when price OK
    "pv_limiter_threshold_ct":   0.0,    # trigger below this price (ct/kWh); 0 = only negative
    "pv_limiter_margin_w":       200,    # extra buffer above house+bat load to avoid oscillation
    # Custom HA service mode (e.g. SMA Devices Plus)
    "pv_limiter_use_service":    False,  # True = use custom service instead of number.set_value
    "pv_limiter_service":        "",     # e.g. "pysmaplus.set_value"
    "pv_limiter_service_param_key":   "entity_id",  # data key alongside "value": "entity_id" or "parameter"
    "pv_limiter_service_param":  "",     # value for that key, e.g. "sensor.sb4_0_active_power_limitation"
    # Strategy engine: "rule_based" (default) or "claude" (uses Anthropic API)
    "strategy_mode":        "rule_based",
    # Anthropic API key (only used when strategy_mode = "claude")
    "claude_api_key":       "",
    # Claude model to use for planning (Sonnet = recommended; Haiku = cheapest/fastest)
    "claude_model":         "claude-sonnet-4-6",
}


def load_strategy_settings() -> dict:
    try:
        with open(STRATEGY_SETTINGS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
        return {**DEFAULT_SETTINGS, **stored}
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_strategy_settings(patch: dict) -> dict:
    current = load_strategy_settings()
    current.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    with open(STRATEGY_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return current


# ---------------------------------------------------------------------------
# Action constants
# ---------------------------------------------------------------------------

SOLAR_CHARGE = "solar_charge"
GRID_CHARGE  = "grid_charge"
SAVE         = "save"
DISCHARGE    = "discharge"
NEUTRAL      = "neutral"


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

def build_plan(
    prices: list[dict],          # [{from, till, marketPrice, ...}] sorted asc
    solar_wh: dict[str, float],  # {slot_key: Wh} from forecast.solar watt_hours_period
    consumption_by_hour: list[dict],  # [{hour: int, avg_wh: float}]  0..23
    bat_soc_now: float,          # current SOC 0..100
    settings: Optional[dict] = None,
    start_dt: Optional[datetime] = None,  # force a specific start time (historical mode)
    num_slots: int = 48,         # number of hourly slots to simulate
) -> list[dict]:
    """
    Build a 48-slot (hourly) charging plan for today + tomorrow.
    Returns list of slot dicts sorted by time.
    """
    s = settings or load_strategy_settings()

    cap_kwh       = float(s["bat_capacity_kwh"])
    rte           = float(s["rte"])
    depr          = float(s["depreciation_eur_kwh"])
    min_soc       = float(s["min_reserve_soc"]) / 100.0
    max_soc       = float(s["max_soc"]) / 100.0
    max_charge_kw = float(s["max_charge_kw"])
    markup        = float(s["grid_markup_eur_kwh"])
    tz_name       = s.get("timezone", "Europe/Brussels")
    tz            = ZoneInfo(tz_name)
    manual_peaks  = s.get("manual_peak_hours", [])

    # Consumption lookup — supports both weekday-aware {(weekday, hour): avg_Wh}
    # and legacy {hour: avg_Wh} formats.
    cons_by_wd_hour: dict[tuple, float] = {}
    cons_by_hour:    dict[int,   float] = {}
    for x in (consumption_by_hour or []):
        h  = int(x["hour"])
        v  = float(x["avg_wh"])
        wd = x.get("weekday")
        if wd is not None:
            cons_by_wd_hour[(int(wd), h)] = v
        else:
            cons_by_hour[h] = v

    has_wd_data = bool(cons_by_wd_hour)

    def _cons(weekday: int, hour: int) -> float:
        if has_wd_data:
            return cons_by_wd_hour.get((weekday, hour),
                   cons_by_hour.get(hour, 300.0))
        return cons_by_hour.get(hour, 300.0)

    # ── Build 48 hourly price slots ──────────────────────────────────────────
    # Expand prices to 1-hour buckets if they're quarter-hour.
    # Always convert to local timezone so keys match the all_slots keys below,
    # regardless of whether the source is ENTSO-E (already local) or Frank (UTC).
    price_by_slot: dict[str, float] = {}  # key = "YYYY-MM-DDTHH:00+offset" local
    for p in prices:
        try:
            dt_raw = datetime.fromisoformat(p["from"])
            if dt_raw.tzinfo is None:
                dt_local = dt_raw.replace(tzinfo=tz)
            else:
                dt_local = dt_raw.astimezone(tz)
            # Round down to hour
            slot_key = dt_local.replace(minute=0, second=0, microsecond=0).isoformat()
            # Average if multiple sub-hour entries
            existing = price_by_slot.get(slot_key, [])
            if isinstance(existing, list):
                existing.append(float(p["marketPrice"]))
                price_by_slot[slot_key] = existing
            else:
                price_by_slot[slot_key] = [existing, float(p["marketPrice"])]
        except Exception:
            pass

    price_slots: dict[str, float] = {}
    for k, v in price_by_slot.items():
        price_slots[k] = sum(v) / len(v) if isinstance(v, list) else v

    # ── Solar Wh per hour ────────────────────────────────────────────────────
    # solar_wh keys: "YYYY-MM-DD HH:MM:SS", aggregate to hour
    solar_by_slot: dict[str, float] = {}
    for k, wh in (solar_wh or {}).items():
        try:
            dt_str = k if "T" in k else k.replace(" ", "T")
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone(tz)
            slot_key = dt.replace(minute=0, second=0, microsecond=0).isoformat()
            solar_by_slot[slot_key] = solar_by_slot.get(slot_key, 0.0) + float(wh)
        except Exception:
            pass

    # ── Generate hourly window ───────────────────────────────────────────────
    real_now = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    if start_dt is not None:
        now_local = start_dt.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    else:
        # Start from midnight of today so the full day is visible
        now_local = real_now.replace(hour=0, minute=0, second=0, microsecond=0)
    all_slots   = [now_local + timedelta(hours=i) for i in range(num_slots)]

    # Gather all prices for statistics
    price_vals = [price_slots.get(sl.isoformat(), None) for sl in all_slots]
    known_prices = [p for p in price_vals if p is not None]

    if known_prices:
        sorted_prices = sorted(known_prices)
        n = len(sorted_prices)
        p25 = sorted_prices[int(n * 0.25)]
        p75 = sorted_prices[int(n * 0.75)]
        price_median = sorted_prices[n // 2]
    else:
        p25 = p75 = price_median = 0.10

    # ── Standby / parasitic consumption ─────────────────────────────────────
    # Auto-detect from 04:00–06:00 historical average (sleeping hours).
    # Falls back to the manual setting value, then to 0.
    _STANDBY_HOURS = {4, 5}
    _configured_standby = float(s.get("standby_w", 0))

    if _configured_standby > 0:
        standby_w = _configured_standby
    else:
        _standby_vals: list[float] = []
        for h in _STANDBY_HOURS:
            if has_wd_data:
                for wd in range(7):
                    v = cons_by_wd_hour.get((wd, h))
                    if v is not None:
                        _standby_vals.append(v)
            else:
                v = cons_by_hour.get(h)
                if v is not None:
                    _standby_vals.append(v)
        standby_w = sum(_standby_vals) / len(_standby_vals) if _standby_vals else 0.0

    log.debug("strategy: standby_w=%.0f W (%s)",
              standby_w, "configured" if _configured_standby > 0 else "auto-detected")

    # ── Determine peak hours ─────────────────────────────────────────────────
    # Peak = top 25% of consumption *above* standby baseline so that
    # always-on hours (night standby ~02–06) are never wrongly flagged as peak.
    def _excess(wh: float) -> float:
        """Consumption above standby baseline."""
        return max(0.0, wh - standby_w)

    if manual_peaks:
        _manual_set = set(int(h) for h in manual_peaks)
        def _is_peak(weekday: int, hour: int) -> bool:
            return hour in _manual_set

    elif has_wd_data:
        _wd_peaks: dict[int, set] = {}
        for wd in range(7):
            wd_excess = {h: _excess(cons_by_wd_hour.get((wd, h), 0.0)) for h in range(24)}
            sorted_v  = sorted(wd_excess.values())
            threshold = sorted_v[int(24 * 0.75)]
            # Only flag hours that actually have meaningful excess above standby
            _wd_peaks[wd] = {h for h, e in wd_excess.items()
                             if e >= threshold and e > standby_w * 0.20}

        def _is_peak(weekday: int, hour: int) -> bool:
            return hour in _wd_peaks.get(weekday, {7, 8, 9, 17, 18, 19, 20, 21})

    elif cons_by_hour:
        _excess_vals = {h: _excess(c) for h, c in cons_by_hour.items()}
        sorted_v  = sorted(_excess_vals.values())
        threshold = sorted_v[int(len(sorted_v) * 0.75)]
        _fallback_peaks = {h for h, e in _excess_vals.items()
                           if e >= threshold and e > standby_w * 0.20}

        def _is_peak(weekday: int, hour: int) -> bool:
            return hour in _fallback_peaks

    else:
        def _is_peak(weekday: int, hour: int) -> bool:
            return hour in {7, 8, 9, 17, 18, 19, 20, 21}

    # ── Simulate battery state over time ────────────────────────────────────
    bat_kwh = cap_kwh * (bat_soc_now / 100.0)
    bat_min = cap_kwh * min_soc
    bat_max = cap_kwh * max_soc

    slots: list[dict] = []

    for i, slot_dt in enumerate(all_slots):
        # Snap to actual SOC at the current hour so that future predictions
        # start from the real battery state, not from a simulated past that
        # may have diverged from reality.
        if slot_dt == real_now:
            bat_kwh = cap_kwh * (bat_soc_now / 100.0)

        slot_key = slot_dt.isoformat()
        hour     = slot_dt.hour
        weekday  = slot_dt.weekday()   # 0 = Monday, 6 = Sunday
        price_raw = price_slots.get(slot_key)
        buy_price = (price_raw + markup) if price_raw is not None else None

        solar_wh_slot  = solar_by_slot.get(slot_key, 0.0)
        cons_wh_slot   = _cons(weekday, hour)
        net_wh         = solar_wh_slot - cons_wh_slot   # positive = solar excess

        soc_start = (bat_kwh / cap_kwh) * 100.0

        action  = NEUTRAL
        reason  = ""
        charge_kwh = 0.0   # energy added to battery this slot (kWh)
        discharge_kwh = 0.0

        # ── Decision logic ───────────────────────────────────────────────────

        if net_wh > 50:
            # Solar excess: charge battery from solar (free)
            absorb_kwh = min(net_wh / 1000.0, bat_max - bat_kwh, max_charge_kw)
            if absorb_kwh > 0.05:
                bat_kwh   += absorb_kwh * rte
                charge_kwh = absorb_kwh
                action = SOLAR_CHARGE
                reason = f"Zonne-overschot {solar_wh_slot:.0f} Wh"
            else:
                action = NEUTRAL
                reason = "Batterij vol of minimale overschot"

        elif buy_price is not None:
            # Look ahead: max price in next 8 hours (for charge profitability)
            future_prices_8 = []
            for j in range(i + 1, min(i + 9, num_slots)):
                fp = price_slots.get(all_slots[j].isoformat())
                if fp is not None:
                    future_prices_8.append(fp + markup)
            max_future = max(future_prices_8) if future_prices_8 else buy_price

            # Best price in next 16 hours (for discharge reservation decisions)
            future_prices_16 = []
            for j in range(i + 1, min(i + 17, num_slots)):
                fp = price_slots.get(all_slots[j].isoformat())
                if fp is not None:
                    future_prices_16.append(fp + markup)
            best_future_16 = max(future_prices_16) if future_prices_16 else buy_price

            is_peak_hour = _is_peak(weekday, hour)

            # Effective charge cost = buy_price / rte + charge depreciation.
            eff_charge_cost = buy_price / rte + depr   # €/kWh stored
            charge_spread   = max_future - eff_charge_cost
            min_spread      = s.get("min_charge_spread_eur_kwh", 0.05)
            is_cheap        = buy_price < p25 * 1.05
            grid_charge_ok  = charge_spread >= min_spread or (is_cheap and charge_spread > 0)

            # Solar-fill check: if remaining solar today would fill the battery
            # without grid charging, skip grid_charge (solar is free).
            remaining_solar_today_wh = sum(
                solar_by_slot.get(all_slots[j].isoformat(), 0.0)
                for j in range(i, num_slots)
                if all_slots[j].date() == slot_dt.date()
            )
            solar_fills_battery = (
                remaining_solar_today_wh / 1000.0 * rte >= (bat_max - bat_kwh) - 0.1
            )

            if buy_price < 0 and bat_kwh < bat_max - 0.05:
                # Negative price: consuming from grid is FREE or PAID.
                # Charge at full rate — also prevents solar export which costs money.
                can_add_kwh = bat_max - bat_kwh
                charge_kwh  = min(can_add_kwh / rte, max_charge_kw)
                bat_kwh    += charge_kwh * rte
                action = GRID_CHARGE
                reason = f"Negatieve prijs ({buy_price*100:.1f}ct) – laden = gratis/betaald"

            elif is_peak_hour and bat_kwh > bat_min + 0.2 and buy_price >= price_median:
                # Peak hour AND price is at or above the day's median.
                # If a much better (>15%) discharge opportunity is within 16h,
                # hold the charge for that instead.
                if best_future_16 > buy_price * 1.15:
                    action = SAVE
                    reason = f"Sparen voor duurder uur ({best_future_16*100:.0f}ct)"
                else:
                    discharge_possible = min(cons_wh_slot / 1000.0, bat_kwh - bat_min)
                    if discharge_possible > 0.05:
                        bat_kwh      -= discharge_possible
                        discharge_kwh = discharge_possible
                        action = DISCHARGE
                        reason = f"Piekuur verbruik ~{cons_wh_slot:.0f} Wh"
                    else:
                        action = NEUTRAL
                        reason = "Batterij te leeg voor ontladen"

            elif grid_charge_ok and bat_kwh < bat_max - 0.2 and not solar_fills_battery:
                # Spread large enough → charge from grid now to discharge later
                can_add_kwh  = bat_max - bat_kwh
                charge_kwh   = min(can_add_kwh / rte, max_charge_kw)
                bat_kwh     += charge_kwh * rte
                action = GRID_CHARGE
                reason = (f"Spread {charge_spread*100:.1f}ct/kWh "
                          f"(koop {buy_price*100:.1f}ct → piek {max_future*100:.1f}ct)")

            elif buy_price > p75 and bat_kwh > bat_min + 0.2:
                # Expensive slot: use battery — but save for even better hours
                if best_future_16 > buy_price * 1.15:
                    action = SAVE
                    reason = f"Sparen voor duurder uur ({best_future_16*100:.0f}ct)"
                else:
                    discharge_possible = min(cons_wh_slot / 1000.0, bat_kwh - bat_min)
                    if discharge_possible > 0.05:
                        bat_kwh      -= discharge_possible
                        discharge_kwh = discharge_possible
                        action = DISCHARGE
                        reason = f"Duur net ({buy_price*100:.1f}ct/kWh)"

            elif bat_kwh > bat_min + 0.3:
                # Battery has charge — decide whether to save or go neutral
                upcoming_peak = any(
                    _is_peak(all_slots[j].weekday(), all_slots[j].hour)
                    for j in range(i + 1, min(i + 6, num_slots))
                )
                # A much more expensive hour is coming soon (30% above current AND above p75)
                better_soon = best_future_16 > buy_price * (1.0 + s.get("save_price_factor", 0.30)) and best_future_16 > p75

                if buy_price > price_median and upcoming_peak:
                    action = SAVE
                    reason = "Sparen voor komende piekuren"
                elif better_soon:
                    action = SAVE
                    reason = f"Goedkoop nu ({buy_price*100:.0f}ct) – sparen voor {best_future_16*100:.0f}ct"
                else:
                    action = NEUTRAL
            else:
                action = NEUTRAL

        # ── Neutral SOC simulation ────────────────────────────────────────
        # anti-feed: battery covers net consumption when no explicit charge/
        # discharge action is set.  Without this the predicted SOC stays flat
        # overnight which is misleading (sluipverbruik drains the battery).
        if action == NEUTRAL:
            if net_wh >= 0:
                surplus_kwh = (net_wh / 1000.0) * rte
                headroom    = (max_soc * cap_kwh) - bat_kwh
                store       = min(surplus_kwh, headroom)
                if store > 0:
                    bat_kwh += store
            else:
                avail = bat_kwh - (min_soc * cap_kwh)
                use   = min((-net_wh) / 1000.0, avail)
                if use > 0:
                    bat_kwh -= use

        soc_end = (bat_kwh / cap_kwh) * 100.0

        slots.append({
            "time":           slot_key,
            "hour":           hour,
            "price_eur_kwh":  round(buy_price, 4) if buy_price is not None else None,
            "price_raw":      round(price_raw, 4) if price_raw is not None else None,
            "solar_wh":       round(solar_wh_slot, 0),
            "consumption_wh": round(cons_wh_slot, 0),
            "net_wh":         round(net_wh, 0),
            "action":         action,
            "reason":         reason,
            "charge_kwh":     round(charge_kwh, 3),
            "discharge_kwh":  round(discharge_kwh, 3),
            "soc_start":      round(soc_start, 1),
            "soc_end":        round(soc_end, 1),
            "is_peak":        _is_peak(weekday, hour),
            "is_past":        slot_dt < real_now,
        })

    return slots


# ---------------------------------------------------------------------------
# Convenience: split today / tomorrow
# ---------------------------------------------------------------------------

def read_soc_cache(soc_file: str, max_age_s: float = 300) -> Optional[float]:
    """Return SOC from a last_soc.json cache file if it is fresher than max_age_s.

    Returns None when the file is missing, unreadable, stale, or contains an
    out-of-range value.  Extracted here so it can be unit-tested without Flask.
    """
    import time
    try:
        with open(soc_file, encoding="utf-8") as f:
            data = json.load(f)
        age_s = time.time() - data.get("ts", 0)
        if age_s < max_age_s:
            val = float(data["soc"])
            if 0.0 <= val <= 100.0:
                return val
    except Exception:
        pass
    return None


def split_days(slots: list[dict]) -> dict:
    today_str    = datetime.now().date().isoformat()
    tomorrow_str = (datetime.now().date() + timedelta(days=1)).isoformat()
    return {
        "today":    [s for s in slots if s["time"].startswith(today_str)],
        "tomorrow": [s for s in slots if s["time"].startswith(tomorrow_str)],
        "all":      slots,
    }
