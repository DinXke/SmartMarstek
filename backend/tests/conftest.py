"""Shared fixtures and helpers for SmartMarstek strategy unit tests."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Brussels")

# Fixed past date (Tuesday) so tests are deterministic and independent of clock.
TEST_START = datetime(2025, 1, 14, 0, 0, 0, tzinfo=TZ)  # 2025-01-14, weekday=1

# Minimal settings with no markup so price arithmetic is straightforward.
BASE_SETTINGS = {
    "bat_capacity_kwh": 10.0,
    "rte": 0.85,
    "depreciation_eur_kwh": 0.06,
    "min_reserve_soc": 10,
    "max_soc": 95,
    "max_charge_kw": 3.0,
    "sell_back": False,
    "timezone": "Europe/Brussels",
    "manual_peak_hours": [],
    "history_days": 21,
    "grid_markup_eur_kwh": 0.0,
    "price_source": "entsoe",
    "consumption_source": "auto",
    "standby_w": 0,
    "save_price_factor": 0.30,
    "min_charge_spread_eur_kwh": 0.05,
    "pv_limiter_enabled": False,
    "pv_limiter_entity": "",
    "pv_limiter_max_w": 4000,
    "pv_limiter_threshold_ct": 0.0,
    "pv_limiter_margin_w": 200,
    "pv_limiter_use_service": False,
    "pv_limiter_service": "",
    "pv_limiter_service_param_key": "entity_id",
    "pv_limiter_service_param": "",
    "strategy_mode": "rule_based",
    "claude_api_key": "",
    "claude_model": "claude-sonnet-4-6",
}


def make_prices(start_dt=None, hourly_prices=None, num_hours=48, flat=None):
    """Build price list for build_plan().

    Pass `flat` for a uniform price across all slots, or `hourly_prices` for
    per-slot values (cycled/truncated to num_hours).
    """
    start_dt = start_dt or TEST_START
    if flat is not None:
        vals = [flat] * num_hours
    else:
        vals = (list(hourly_prices) * ((num_hours // len(hourly_prices)) + 1))[:num_hours]
    return [
        {"from": (start_dt + timedelta(hours=i)).isoformat(), "marketPrice": v}
        for i, v in enumerate(vals)
    ]


def make_consumption(hourly_wh=300.0):
    """Flat hourly consumption profile (no weekday distinction)."""
    return [{"hour": h, "avg_wh": float(hourly_wh)} for h in range(24)]


def settings(**overrides):
    """Return BASE_SETTINGS with optional overrides."""
    return {**BASE_SETTINGS, **overrides}
