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

STRATEGY_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "strategy_settings.json")

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

    # Consumption lookup {hour: avg_Wh}
    cons_by_hour = {int(x["hour"]): float(x["avg_wh"]) for x in (consumption_by_hour or [])}

    # ── Build 48 hourly price slots ──────────────────────────────────────────
    # Expand prices to 1-hour buckets if they're quarter-hour
    price_by_slot: dict[str, float] = {}  # key = "YYYY-MM-DDTHH:00" local
    for p in prices:
        try:
            dt_local = datetime.fromisoformat(p["from"])
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

    # ── Determine peak hours ─────────────────────────────────────────────────
    if manual_peaks:
        peak_hours = set(int(h) for h in manual_peaks)
    elif cons_by_hour:
        # Top 25% consumption hours = peak
        cons_vals = list(cons_by_hour.values())
        threshold = sorted(cons_vals)[int(len(cons_vals) * 0.75)]
        peak_hours = {h for h, c in cons_by_hour.items() if c >= threshold}
    else:
        # Default peak: morning 7-9 and evening 17-22
        peak_hours = {7, 8, 9, 17, 18, 19, 20, 21}

    # ── Simulate battery state over time ────────────────────────────────────
    bat_kwh = cap_kwh * (bat_soc_now / 100.0)
    bat_min = cap_kwh * min_soc
    bat_max = cap_kwh * max_soc

    slots: list[dict] = []

    for i, slot_dt in enumerate(all_slots):
        slot_key = slot_dt.isoformat()
        hour     = slot_dt.hour
        price_raw = price_slots.get(slot_key)
        buy_price = (price_raw + markup) if price_raw is not None else None

        solar_wh_slot  = solar_by_slot.get(slot_key, 0.0)
        cons_wh_slot   = cons_by_hour.get(hour, 300.0)  # default 300 Wh/h if no history
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
            # Look ahead: max price in next 8 hours
            future_prices = []
            for j in range(i + 1, min(i + 9, num_slots)):
                fp = price_slots.get(all_slots[j].isoformat())
                if fp is not None:
                    future_prices.append(fp + markup)
            max_future = max(future_prices) if future_prices else buy_price
            is_peak_hour = (hour in peak_hours)

            # Effective charge cost = buy_price / rte + depreciation
            eff_charge_cost = buy_price / rte + depr

            # Worth charging from grid if we can sell (save) at a higher future price
            can_profit = (eff_charge_cost + depr) < (max_future - depr)
            is_cheap   = buy_price < p25 * 1.05

            if is_peak_hour and bat_kwh > bat_min + 0.2:
                # Peak consumption hour: discharge battery
                discharge_possible = min(cons_wh_slot / 1000.0, bat_kwh - bat_min)
                if discharge_possible > 0.05:
                    bat_kwh      -= discharge_possible
                    discharge_kwh = discharge_possible
                    action = DISCHARGE
                    reason = f"Piekuur verbruik ~{cons_wh_slot:.0f} Wh"
                else:
                    action = NEUTRAL
                    reason = "Batterij te leeg voor ontladen"

            elif is_cheap and can_profit and bat_kwh < bat_max - 0.2:
                # Cheap grid electricity + future savings justify charging
                can_add_kwh  = bat_max - bat_kwh
                charge_kwh   = min(can_add_kwh / rte, max_charge_kw)
                bat_kwh     += charge_kwh * rte
                action = GRID_CHARGE
                reason = (f"Goedkoop ({buy_price*100:.1f}ct/kWh) → "
                          f"piek later {max_future*100:.1f}ct")

            elif buy_price > p75 and bat_kwh > bat_min + 0.2:
                # Expensive slot: use battery
                discharge_possible = min(cons_wh_slot / 1000.0, bat_kwh - bat_min)
                if discharge_possible > 0.05:
                    bat_kwh      -= discharge_possible
                    discharge_kwh = discharge_possible
                    action = DISCHARGE
                    reason = f"Duur net ({buy_price*100:.1f}ct/kWh)"

            elif bat_kwh > bat_min + 0.3 and buy_price > price_median:
                # Battery has charge and upcoming peak: hold it
                upcoming_peak = any(
                    all_slots[j].hour in peak_hours
                    for j in range(i + 1, min(i + 6, num_slots))
                )
                if upcoming_peak:
                    action = SAVE
                    reason = "Sparen voor komende piekuren"
                else:
                    action = NEUTRAL
            else:
                action = NEUTRAL

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
            "is_peak":        hour in peak_hours,
            "is_past":        slot_dt < real_now,
        })

    return slots


# ---------------------------------------------------------------------------
# Convenience: split today / tomorrow
# ---------------------------------------------------------------------------

def split_days(slots: list[dict]) -> dict:
    today_str    = datetime.now().date().isoformat()
    tomorrow_str = (datetime.now().date() + timedelta(days=1)).isoformat()
    return {
        "today":    [s for s in slots if s["time"].startswith(today_str)],
        "tomorrow": [s for s in slots if s["time"].startswith(tomorrow_str)],
        "all":      slots,
    }
