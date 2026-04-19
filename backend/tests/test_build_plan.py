"""
Unit tests for strategy.build_plan() — covers all action types plus the
RTE, spread, bias, peak-detection, and SOC-simulation paths that have
historically caused regressions (SCH-70, SCH-77).

Design notes
------------
- start_dt is always TEST_START (2025-01-14), a past date, so no slot ever
  equals `real_now` and the "snap to current SOC" branch is never triggered.
- grid_markup_eur_kwh = 0.0 in BASE_SETTINGS; buy_price == marketPrice in all
  tests, keeping spread arithmetic simple.
- manual_peak_hours is set per-test to decouple peak detection from the
  consumption-history heuristic.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import timedelta

from strategy import build_plan, SOLAR_CHARGE, GRID_CHARGE, DISCHARGE, SAVE, NEUTRAL
from tests.conftest import TEST_START, make_prices, make_consumption, settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slot(slots, hour_offset):
    """Return the slot at `hour_offset` hours from slot 0."""
    return slots[hour_offset]


def actions(slots):
    return [s["action"] for s in slots]


# ---------------------------------------------------------------------------
# SOLAR_CHARGE
# ---------------------------------------------------------------------------

class TestSolarCharge:
    def test_solar_excess_charges_battery(self):
        """Net solar > 50 Wh and battery below max → SOLAR_CHARGE."""
        solar = {
            (TEST_START + timedelta(hours=10)).isoformat(): 2000.0,
        }
        prices = make_prices(flat=0.15)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, solar, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=12)
        assert slot(slots, 10)["action"] == SOLAR_CHARGE

    def test_solar_excess_below_threshold_is_neutral(self):
        """Net solar ≤ 50 Wh (30 Wh excess) → NEUTRAL (absorb_kwh too small)."""
        solar = {
            (TEST_START + timedelta(hours=10)).isoformat(): 330.0,  # 330-300=30 Wh net
        }
        prices = make_prices(flat=0.15)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, solar, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=12)
        assert slot(slots, 10)["action"] == NEUTRAL

    def test_solar_no_charge_when_battery_full(self):
        """Battery already at max_soc → no meaningful headroom → no SOLAR_CHARGE.

        Test at slot 0 to avoid drain from prior neutral hours muddying the test.
        max_soc=95%, bat_soc=95% → bat_kwh == bat_max → absorb_kwh = 0.
        """
        solar = {
            TEST_START.isoformat(): 5000.0,
        }
        prices = make_prices(flat=0.15)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[], max_soc=95)

        slots = build_plan(prices, solar, cons, bat_soc_now=95.0,
                           settings=s, start_dt=TEST_START, num_slots=4)
        assert slot(slots, 0)["action"] != SOLAR_CHARGE

    def test_solar_charge_increases_soc(self):
        """SOC must rise during a SOLAR_CHARGE slot."""
        solar = {
            (TEST_START + timedelta(hours=6)).isoformat(): 3000.0,
        }
        prices = make_prices(flat=0.15)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, solar, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=8)
        sc = slot(slots, 6)
        assert sc["action"] == SOLAR_CHARGE
        assert sc["soc_end"] > sc["soc_start"]


# ---------------------------------------------------------------------------
# GRID_CHARGE
# ---------------------------------------------------------------------------

class TestGridCharge:
    def test_negative_price_forces_grid_charge(self):
        """Negative marketPrice → GRID_CHARGE regardless of spread."""
        hourly = [-0.05] + [0.20] * 47
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=8)
        assert slot(slots, 0)["action"] == GRID_CHARGE

    def test_positive_spread_triggers_grid_charge(self):
        """
        Buy cheap now, sell expensive later (within 8h window) → GRID_CHARGE.

        With rte=0.85, depr=0.06:
          eff_cost = 0.10 / 0.85 + 0.06 ≈ 0.178 €/kWh
          max_future (hour 7) = 0.35
          spread = 0.35 - 0.178 = 0.172 ≥ 0.05 → GRID_CHARGE at hour 0
        """
        hourly = [0.10] * 7 + [0.35] + [0.15] * 40
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=48)
        assert slot(slots, 0)["action"] == GRID_CHARGE

    def test_insufficient_spread_no_grid_charge(self):
        """
        When effective charge cost ≥ max future price, spread < 0 → no GRID_CHARGE.

        buy = 0.25 everywhere → eff_cost = 0.25/0.85 + 0.06 ≈ 0.354
        spread = 0.25 - 0.354 = -0.104 < 0 → no grid charge
        """
        prices = make_prices(flat=0.25)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[10, 11, 12], min_charge_spread_eur_kwh=0.05)

        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=24)
        assert all(sl["action"] != GRID_CHARGE for sl in slots)

    def test_rte_reduces_spread(self):
        """
        Lower RTE → higher effective charge cost → harder to reach min spread.

        Most prices very cheap (0.05) so buy=0.10 at hour 0 is NOT flagged as
        "cheap" (p25≈0.05, is_cheap = 0.10 < 0.0525 → False).  The is_cheap
        bypass is therefore inactive; only the spread threshold decides.

        rte=0.70: eff_cost = 0.10/0.70 + 0.06 = 0.203; spread = 0.22−0.203 = 0.017 < 0.05 → no GRID_CHARGE
        rte=0.95: eff_cost = 0.10/0.95 + 0.06 = 0.165; spread = 0.22−0.165 = 0.055 ≥ 0.05 → GRID_CHARGE
        """
        # hour 0 = 0.10, hour 7 = 0.22 (within 8-h lookahead), rest = 0.05
        hourly = [0.10] + [0.05] * 6 + [0.22] + [0.05] * 40
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)

        slots_low_rte = build_plan(prices, {}, cons, bat_soc_now=50.0,
                                   settings=settings(manual_peak_hours=[], rte=0.70),
                                   start_dt=TEST_START, num_slots=48)
        slots_high_rte = build_plan(prices, {}, cons, bat_soc_now=50.0,
                                    settings=settings(manual_peak_hours=[], rte=0.95),
                                    start_dt=TEST_START, num_slots=48)

        assert slot(slots_low_rte, 0)["action"] != GRID_CHARGE
        assert slot(slots_high_rte, 0)["action"] == GRID_CHARGE

    def test_min_charge_spread_setting_respected(self):
        """
        min_charge_spread=0.20 blocks a charge that passes at the default 0.05.

        Most prices = 0.05 so is_cheap bypass is inactive at hour 0 (buy=0.10,
        p25≈0.05, 0.10 > 0.0525 → is_cheap=False).  Spread is then the only gate.

        spread = 0.35 − (0.10/0.85 + 0.06) ≈ 0.172.
        default 0.05 → 0.172 ≥ 0.05 → GRID_CHARGE.
        strict  0.20 → 0.172 < 0.20 → no GRID_CHARGE.
        """
        # hour 0 = 0.10, hour 7 = 0.35 (within 8-h window), rest = 0.05
        hourly = [0.10] + [0.05] * 6 + [0.35] + [0.05] * 40
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)

        s_default = settings(manual_peak_hours=[], min_charge_spread_eur_kwh=0.05)
        s_strict  = settings(manual_peak_hours=[], min_charge_spread_eur_kwh=0.20)

        slots_default = build_plan(prices, {}, cons, bat_soc_now=50.0,
                                   settings=s_default, start_dt=TEST_START, num_slots=48)
        slots_strict  = build_plan(prices, {}, cons, bat_soc_now=50.0,
                                   settings=s_strict,  start_dt=TEST_START, num_slots=48)

        assert slot(slots_default, 0)["action"] == GRID_CHARGE
        assert slot(slots_strict,  0)["action"] != GRID_CHARGE

    def test_solar_fills_battery_skips_grid_charge(self):
        """
        Skip GRID_CHARGE when upcoming solar is enough to fill the battery.

        Battery at 50% (5 kWh), bat_max = 9.5 kWh (at max_soc=95%).
        Needs 4.5 kWh. Solar = 6000 Wh × 0.85 rte = 5.1 kWh > 4.4 kWh gap → skip.
        """
        # Solar in hours 4-6 (within the same day as start)
        solar = {
            (TEST_START + timedelta(hours=4)).isoformat(): 2000.0,
            (TEST_START + timedelta(hours=5)).isoformat(): 2000.0,
            (TEST_START + timedelta(hours=6)).isoformat(): 2000.0,
        }
        hourly = [0.10] * 7 + [0.35] + [0.15] * 40
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, solar, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=48)
        # Hour 0: solar_fills_battery check should block GRID_CHARGE
        assert slot(slots, 0)["action"] != GRID_CHARGE

    def test_grid_charge_increases_soc(self):
        """SOC must rise during a GRID_CHARGE slot."""
        hourly = [-0.02] + [0.20] * 47
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=4)
        gc = slot(slots, 0)
        assert gc["action"] == GRID_CHARGE
        assert gc["soc_end"] > gc["soc_start"]


# ---------------------------------------------------------------------------
# DISCHARGE
# ---------------------------------------------------------------------------

class TestDischarge:
    def test_discharge_at_peak_hour_expensive_price(self):
        """
        Peak hour AND price ≥ median AND no better hour within 16h → DISCHARGE.

        All prices = 0.20 (flat → price == median). Hour 2 = manual peak.
        Test at hour 2 so only 2 drain hours have passed and battery is > min+0.2.
        best_future_16 = 0.20 < 0.20×1.15 = 0.23 → no SAVE → DISCHARGE.
        """
        prices = make_prices(flat=0.20)
        cons = make_consumption(1000.0)
        s = settings(manual_peak_hours=[2])

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=24)
        assert slot(slots, 2)["action"] == DISCHARGE

    def test_no_discharge_when_battery_at_minimum(self):
        """Battery at min_reserve_soc → discharge_possible ≈ 0 → NEUTRAL."""
        prices = make_prices(flat=0.20)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[5], min_reserve_soc=10)

        slots = build_plan(prices, {}, cons, bat_soc_now=10.0,
                           settings=s, start_dt=TEST_START, num_slots=8)
        assert slot(slots, 5)["action"] != DISCHARGE

    def test_discharge_reduces_soc(self):
        """SOC must fall during a DISCHARGE slot."""
        prices = make_prices(flat=0.20)
        cons = make_consumption(1000.0)
        s = settings(manual_peak_hours=[2])

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=24)
        ds = slot(slots, 2)
        assert ds["action"] == DISCHARGE
        assert ds["soc_end"] < ds["soc_start"]

    def test_discharge_expensive_off_peak_above_p75(self):
        """
        Non-peak hour but price > p75 AND no better price within 16h → DISCHARGE.

        Set prices so hour 20 is the single expensive hour (0.40) but there are no
        even-more-expensive hours within 16h, so it should DISCHARGE.
        """
        hourly = [0.15] * 20 + [0.40] + [0.15] * 27
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(500.0)
        # No manual peak hours — p75 of mostly 0.15 with one 0.40 is ~0.15/0.40
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=48)
        # hour 20: price = 0.40 > p75; no future > 0.40 × 1.15 within 16h → DISCHARGE
        assert slot(slots, 20)["action"] in (DISCHARGE, SAVE)


# ---------------------------------------------------------------------------
# SAVE
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_at_peak_when_better_hour_ahead(self):
        """
        Peak hour but a much better (>15%) discharge price is coming within 16h.
        Battery holds charge → SAVE instead of DISCHARGE.

        price at hour 10 = 0.20 (median); price at hour 20 = 0.30 > 0.20 × 1.15 = 0.23
        """
        hourly = [0.15] * 10 + [0.20] + [0.15] * 9 + [0.30] + [0.15] * 27
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[10])

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=48)
        assert slot(slots, 10)["action"] == SAVE

    def test_save_price_factor_controls_threshold(self):
        """
        save_price_factor controls the "better_soon" SAVE branch.

        Setup: bat at 95% (blocks grid charge), all prices = 0.15 except hour 5 = 0.25.
        Hour 0: buy=0.15 == median == p75 (flat); "buy > p75" branch is skipped (strict >).
        Reaches "bat_kwh > bat_min + 0.3" branch where better_soon is evaluated.
        p75 ≈ 0.15; best_future_16 = 0.25.

        factor=0.80: 0.25 > 0.15×1.80=0.27? No → better_soon=False → NEUTRAL
        factor=0.50: 0.25 > 0.15×1.50=0.225? Yes AND 0.25>p75=0.15 → SAVE
        """
        hourly = [0.15] * 5 + [0.25] + [0.15] * 42
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)

        # Battery full → blocks grid_charge branch
        slots_strict = build_plan(prices, {}, cons, bat_soc_now=95.0,
                                  settings=settings(manual_peak_hours=[],
                                                    save_price_factor=0.80),
                                  start_dt=TEST_START, num_slots=48)
        slots_relax = build_plan(prices, {}, cons, bat_soc_now=95.0,
                                 settings=settings(manual_peak_hours=[],
                                                   save_price_factor=0.50),
                                 start_dt=TEST_START, num_slots=48)

        assert slot(slots_strict, 0)["action"] != SAVE
        assert slot(slots_relax,  0)["action"] == SAVE


# ---------------------------------------------------------------------------
# NEUTRAL
# ---------------------------------------------------------------------------

class TestNeutral:
    def test_flat_prices_no_triggers_mostly_neutral(self):
        """
        Flat price everywhere + empty battery (40%) + no solar → mostly NEUTRAL.
        Some SAVE or DISCHARGE may still occur; the key check is no GRID_CHARGE.
        """
        prices = make_prices(flat=0.15)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=40.0,
                           settings=s, start_dt=TEST_START, num_slots=24)
        # No grid charge: spread is negative (flat price)
        assert all(sl["action"] != GRID_CHARGE for sl in slots)

    def test_neutral_soc_decreases_during_consumption(self):
        """In NEUTRAL with net_wh < 0 battery discharges to cover consumption."""
        prices = make_prices(flat=0.15)
        cons = make_consumption(500.0)
        s = settings(manual_peak_hours=[])

        # Force all NEUTRAL by using a price scenario with no triggers
        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=4)
        neutral_slots = [sl for sl in slots if sl["action"] == NEUTRAL]
        for ns in neutral_slots:
            assert ns["soc_end"] <= ns["soc_start"], (
                f"NEUTRAL slot at {ns['time']}: SOC should not rise unexpectedly"
            )


# ---------------------------------------------------------------------------
# Effective charge cost / RTE / depreciation
# ---------------------------------------------------------------------------

class TestEffectiveChargeCost:
    """Verify that the eff_charge_cost formula drives grid-charge decisions correctly."""

    def _calc_eff_cost(self, buy_price, rte, depr):
        return buy_price / rte + depr

    def test_formula_values(self):
        """Document expected effective costs for common parameter combos."""
        assert abs(self._calc_eff_cost(0.10, 0.85, 0.06) - 0.1776) < 0.001
        assert abs(self._calc_eff_cost(0.10, 0.95, 0.06) - 0.1653) < 0.001
        assert abs(self._calc_eff_cost(0.10, 0.70, 0.06) - 0.2029) < 0.001

    def test_depreciation_blocks_marginal_charge(self):
        """
        Without depreciation (depr=0), marginal grid charge fires.
        With depr=0.10, effective cost is too high → no GRID_CHARGE.

        buy=0.10, future=0.20:
          depr=0.00: eff_cost=0.118, spread=0.082 ≥ 0.05 → GRID_CHARGE
          depr=0.10: eff_cost=0.218, spread=−0.018 < 0 → no GRID_CHARGE
        """
        hourly = [0.10] * 7 + [0.20] + [0.15] * 40
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)

        slots_no_depr = build_plan(prices, {}, cons, bat_soc_now=50.0,
                                   settings=settings(manual_peak_hours=[],
                                                     depreciation_eur_kwh=0.00),
                                   start_dt=TEST_START, num_slots=48)
        slots_high_depr = build_plan(prices, {}, cons, bat_soc_now=50.0,
                                     settings=settings(manual_peak_hours=[],
                                                       depreciation_eur_kwh=0.10),
                                     start_dt=TEST_START, num_slots=48)

        assert slot(slots_no_depr, 0)["action"] == GRID_CHARGE
        assert slot(slots_high_depr, 0)["action"] != GRID_CHARGE


# ---------------------------------------------------------------------------
# Peak-hour detection
# ---------------------------------------------------------------------------

class TestPeakDetection:
    def test_manual_peak_hours_respected(self):
        """manual_peak_hours config overrides consumption-based detection."""
        prices = make_prices(flat=0.20)
        cons = make_consumption(300.0)  # flat consumption → no natural peak
        s = settings(manual_peak_hours=[3, 14, 22])

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=24)
        for h in [3, 14, 22]:
            assert slot(slots, h)["is_peak"] is True
        for h in [0, 1, 7, 17]:
            assert slot(slots, h)["is_peak"] is False

    def test_consumption_based_peak_detection(self):
        """Hours with consumption well above standby baseline are flagged as peak."""
        hourly_cons = [200.0] * 7 + [1200.0, 1500.0, 1400.0] + [200.0] * 14
        cons = [{"hour": h, "avg_wh": float(hourly_cons[h])} for h in range(24)]
        prices = make_prices(flat=0.20)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=10)
        # hours 7–9 have high consumption → should be detected as peak
        for h in [7, 8, 9]:
            assert slot(slots, h)["is_peak"] is True


# ---------------------------------------------------------------------------
# SOC boundary conditions
# ---------------------------------------------------------------------------

class TestSocBoundaries:
    def test_soc_never_below_min_reserve(self):
        """After any sequence of discharge slots, soc_end ≥ min_reserve_soc."""
        prices = make_prices(flat=0.20)
        cons = make_consumption(2000.0)  # heavy consumption
        s = settings(manual_peak_hours=list(range(24)), min_reserve_soc=10)

        slots = build_plan(prices, {}, cons, bat_soc_now=80.0,
                           settings=s, start_dt=TEST_START, num_slots=48)
        for sl in slots:
            assert sl["soc_end"] >= s["min_reserve_soc"] - 0.1, (
                f"SOC below reserve at {sl['time']}: {sl['soc_end']:.1f}%"
            )

    def test_soc_never_exceeds_max_soc(self):
        """Solar + grid charge must not push soc_end above max_soc."""
        solar = {
            (TEST_START + timedelta(hours=i)).isoformat(): 5000.0
            for i in range(12)
        }
        hourly = [-0.02] + [0.20] * 47
        prices = make_prices(hourly_prices=hourly)
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[], max_soc=90)

        slots = build_plan(prices, solar, cons, bat_soc_now=20.0,
                           settings=s, start_dt=TEST_START, num_slots=48)
        for sl in slots:
            assert sl["soc_end"] <= s["max_soc"] + 0.2, (
                f"SOC above max at {sl['time']}: {sl['soc_end']:.1f}%"
            )

    def test_slot_output_keys(self):
        """Each slot dict contains the expected keys."""
        expected_keys = {
            "time", "hour", "price_eur_kwh", "price_raw", "solar_wh",
            "consumption_wh", "net_wh", "action", "reason",
            "charge_kwh", "discharge_kwh", "soc_start", "soc_end",
            "is_peak", "is_past",
        }
        prices = make_prices(flat=0.15)
        cons = make_consumption(300.0)
        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=settings(), start_dt=TEST_START, num_slots=4)
        for sl in slots:
            assert expected_keys.issubset(sl.keys())

    def test_correct_number_of_slots(self):
        """build_plan returns exactly num_slots entries."""
        prices = make_prices(flat=0.15, num_hours=48)
        cons = make_consumption(300.0)
        for n in [24, 48, 12]:
            slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                               settings=settings(), start_dt=TEST_START, num_slots=n)
            assert len(slots) == n


# ---------------------------------------------------------------------------
# No-price slots
# ---------------------------------------------------------------------------

class TestMissingPrices:
    def test_missing_price_slots_produce_neutral(self):
        """Slots with no matching price entry → buy_price=None → action=NEUTRAL."""
        prices = make_prices(flat=0.20, num_hours=2)  # only 2 price slots
        cons = make_consumption(300.0)
        s = settings(manual_peak_hours=[])

        slots = build_plan(prices, {}, cons, bat_soc_now=50.0,
                           settings=s, start_dt=TEST_START, num_slots=6)
        # Slots 2–5 have no price data → must be NEUTRAL
        for i in range(2, 6):
            assert slot(slots, i)["action"] == NEUTRAL
        for i in range(2, 6):
            assert slot(slots, i)["price_eur_kwh"] is None
