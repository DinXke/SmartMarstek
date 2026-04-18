"""
strategy_claude.py – Claude AI-powered battery planning engine.

Alternative to the rule-based strategy.py algorithm.
Uses the Anthropic API to generate an hourly battery plan given:
  - Hourly prices (Frank Energie or ENTSO-E, all-in including markup)
  - Solar forecast (Wh per hour)
  - Historical consumption profile (weekday-aware, Wh per hour)
  - Current SoC and battery settings

Returns slots in the same format as strategy.build_plan(), so it can be
used as a drop-in replacement.  Falls back to the rule-based engine on
any error (no API key, network failure, unexpected response, …).

Debug info from the last run is stored in _last_debug and can be
retrieved with get_last_debug().
"""

import json
import logging
import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("strategy_claude")

WEEKDAY_NL = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]

# Per-model pricing (USD per token, converted to EUR at 0.92)
# Cache read tokens are 90% cheaper than uncached input tokens.
_MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"in": 0.80, "out": 4.00, "cache_write": 1.00, "cache_read": 0.08},
    "claude-haiku-4-5":          {"in": 0.80, "out": 4.00, "cache_write": 1.00, "cache_read": 0.08},
    "claude-sonnet-4-6":         {"in": 3.00, "out": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-7":           {"in": 15.00, "out": 75.00, "cache_write": 18.75, "cache_read": 1.50},
}
_USD_TO_EUR = 0.92

def _token_cost_eur(model: str, in_tok: int, out_tok: int,
                    cache_write_tok: int = 0, cache_read_tok: int = 0) -> float:
    p = _MODEL_PRICING.get(model, _MODEL_PRICING["claude-haiku-4-5-20251001"])
    total_usd = (
        in_tok          * p["in"]          / 1_000_000
        + out_tok       * p["out"]         / 1_000_000
        + cache_write_tok * p["cache_write"] / 1_000_000
        + cache_read_tok  * p["cache_read"]  / 1_000_000
    )
    return total_usd * _USD_TO_EUR

# ---------------------------------------------------------------------------
# Usage ledger — persisted to /data/claude_usage.json
# ---------------------------------------------------------------------------

_DATA_DIR           = os.environ.get("MARSTEK_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
_USAGE_FILE          = os.path.join(_DATA_DIR, "claude_usage.json")
_PRICE_HISTORY_FILE  = os.path.join(_DATA_DIR, "_price_history.json")
_SOC_HISTORY_FILE    = os.path.join(_DATA_DIR, "_soc_history.json")
_PLAN_HISTORY_FILE   = os.path.join(_DATA_DIR, "_plan_history.json")
_PLAN_ACCURACY_FILE  = os.path.join(_DATA_DIR, "_plan_accuracy.json")


def _load_usage() -> list[dict]:
    """Load all usage records from disk. Returns list of {ran_at, model, in, out, eur}."""
    try:
        with open(_USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _append_usage(ran_at: str, model: str, in_tok: int, out_tok: int, eur: float) -> None:
    """Append one usage record and prune entries older than 366 days."""
    records = _load_usage()
    records.append({"ran_at": ran_at, "model": model,
                    "in": in_tok, "out": out_tok, "eur": round(eur, 6)})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=366)).isoformat()
    records = [r for r in records if r.get("ran_at", "") >= cutoff]
    try:
        with open(_USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f)
    except Exception as e:
        log.warning("claude_usage: write failed: %s", e)


def get_usage_stats() -> dict:
    """Return aggregated usage: last 1 day / last 7 days / last 31 days / all-time."""
    records = _load_usage()
    now     = datetime.now(timezone.utc)

    cut_1d  = (now - timedelta(days=1)).isoformat()
    cut_7d  = (now - timedelta(days=7)).isoformat()
    cut_31d = (now - timedelta(days=31)).isoformat()

    def _agg(recs):
        calls = len(recs)
        in_t  = sum(r["in"]  for r in recs)
        out_t = sum(r["out"] for r in recs)
        eur   = sum(r["eur"] for r in recs)
        return {"calls": calls, "tokens_in": in_t, "tokens_out": out_t, "eur": round(eur, 5)}

    return {
        "last_1d":  _agg([r for r in records if r.get("ran_at", "") >= cut_1d]),
        "last_7d":  _agg([r for r in records if r.get("ran_at", "") >= cut_7d]),
        "last_31d": _agg([r for r in records if r.get("ran_at", "") >= cut_31d]),
        "all_time": _agg(records),
        "records":  len(records),
    }


# ---------------------------------------------------------------------------
# Historical price pattern storage and analysis
# ---------------------------------------------------------------------------

def _load_price_history() -> dict:
    """Load stored price history. Returns {date_iso: {hour_str: all_in_price}}."""
    try:
        with open(_PRICE_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _record_price_history(price_slots: dict, markup: float) -> None:
    """Persist all-in prices from price_slots into the rolling 32-day history file."""
    history = _load_price_history()
    changed = False
    for key, raw_price in price_slots.items():
        try:
            dt = datetime.fromisoformat(key)
            date_str = dt.date().isoformat()
            hour_str = str(dt.hour)
            all_in = round(raw_price + markup, 4)
            if history.get(date_str, {}).get(hour_str) != all_in:
                history.setdefault(date_str, {})[hour_str] = all_in
                changed = True
        except Exception:
            pass
    if not changed:
        return
    # Prune to last 32 days
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=32)).isoformat()
    history = {k: v for k, v in history.items() if k >= cutoff}
    try:
        with open(_PRICE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f)
    except Exception as e:
        log.warning("price_history: write failed: %s", e)


def _compute_weekly_profile(history: dict) -> dict:
    """Compute per-(weekday × hour) price statistics from stored history.

    Returns a list of dicts (sorted by weekday, hour) — each entry:
      {weekday, weekday_name, hour, avg, p25, p75, count}
    Only includes buckets with at least 2 data points.
    """
    from datetime import date as _date_t

    buckets: dict[tuple, list] = {}
    for date_str, hours in history.items():
        try:
            d = _date_t.fromisoformat(date_str)
            wd = d.weekday()
            for hour_str, price in hours.items():
                buckets.setdefault((wd, int(hour_str)), []).append(float(price))
        except Exception:
            pass

    result = []
    for (wd, h) in sorted(buckets):
        sp = sorted(buckets[(wd, h)])
        n = len(sp)
        if n < 2:
            continue
        result.append({
            "weekday":      wd,
            "weekday_name": WEEKDAY_NL[wd],
            "hour":         h,
            "avg":          round(sum(sp) / n, 4),
            "p25":          round(sp[max(0, int(n * 0.25) - 1)], 4),
            "p75":          round(sp[min(n - 1, int(n * 0.75))], 4),
            "count":        n,
        })
    return result


# ---------------------------------------------------------------------------
# Historical SoC profile (actual battery level per weekday × hour)
# ---------------------------------------------------------------------------

def _get_soc_profile(tz_name: str) -> tuple[list, int]:
    """
    Return (weekly_soc_profile, days_of_history).

    Loads from _soc_history.json cache when fresh (< 6 h old).
    Otherwise fetches the last 32 days from InfluxDB in one bulk query,
    caches the result, and returns the per-(weekday × hour) statistics.

    Profile entries: {weekday, weekday_name, hour, avg, p25, p75, count}
    """
    # ── Try cache first ───────────────────────────────────────────────────
    try:
        with open(_SOC_HISTORY_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        meta    = cached.get("_meta", {})
        updated = meta.get("updated_at", "")
        age_h   = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(updated)).total_seconds() / 3600
        if age_h < 6:
            history = {k: v for k, v in cached.items() if k != "_meta"}
            return _build_soc_profile(history), len(history)
    except Exception:
        pass

    # ── Fetch fresh from InfluxDB ─────────────────────────────────────────
    history: dict = {}
    try:
        from influx_writer import query_soc_history
        history = query_soc_history(days=32, tz_name=tz_name)
    except Exception as exc:
        log.warning("soc_profile: InfluxDB fetch failed: %s", exc)

    # ── Persist cache ─────────────────────────────────────────────────────
    if history:
        try:
            payload = dict(history)
            payload["_meta"] = {"updated_at": datetime.now(timezone.utc).isoformat()}
            with open(_SOC_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as e:
            log.warning("soc_profile: cache write failed: %s", e)

    return _build_soc_profile(history), len(history)


def _build_soc_profile(history: dict) -> list:
    """Group raw {date: {hour: soc}} history into per-(weekday × hour) statistics."""
    from datetime import date as _date_t

    buckets: dict[tuple, list] = {}
    for date_str, hours in history.items():
        try:
            d  = _date_t.fromisoformat(date_str)
            wd = d.weekday()
            for h_key, soc in hours.items():
                buckets.setdefault((wd, int(h_key)), []).append(float(soc))
        except Exception:
            pass

    result = []
    for (wd, h) in sorted(buckets):
        sp = sorted(buckets[(wd, h)])
        n  = len(sp)
        if n < 2:
            continue
        result.append({
            "weekday":      wd,
            "weekday_name": WEEKDAY_NL[wd],
            "hour":         h,
            "avg":          round(sum(sp) / n, 1),
            "p25":          round(sp[max(0, int(n * 0.25) - 1)], 1),
            "p75":          round(sp[min(n - 1, int(n * 0.75))], 1),
            "count":        n,
        })
    return result


# ---------------------------------------------------------------------------
# Plan vs actuals accuracy tracking
# ---------------------------------------------------------------------------

def _save_plan_for_accuracy(result_slots: list, generated_at: str) -> None:
    """Persist future plan slots to _plan_history.json for later accuracy comparison.
    Keyed by ISO hour string; only stores future (non-past) slots.
    Old entries (> 3 days) are pruned automatically.
    """
    try:
        with open(_PLAN_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    history = {k: v for k, v in history.items() if k >= cutoff}

    for slot in result_slots:
        if slot.get("is_past"):
            continue
        key = slot["time"]
        history[key] = {
            "generated_at":   generated_at,
            "action":         slot["action"],
            "soc_start":      slot["soc_start"],
            "solar_wh":       slot["solar_wh"],
            "consumption_wh": slot["consumption_wh"],
        }

    try:
        with open(_PLAN_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f)
    except Exception as e:
        log.warning("plan_history: write failed: %s", e)


def _evaluate_past_plans(tz_name: str) -> None:
    """Compare plan_history slots that are now in the past with InfluxDB actuals.
    Appends comparison records to _plan_accuracy.json (rolling 30 days).
    """
    try:
        with open(_PLAN_HISTORY_FILE, "r", encoding="utf-8") as f:
            plan_history = json.load(f)
    except Exception:
        return

    try:
        with open(_PLAN_ACCURACY_FILE, "r", encoding="utf-8") as f:
            accuracy_log = json.load(f)
    except Exception:
        accuracy_log = []

    # Only evaluate slots that are in the past AND not yet evaluated
    already_evaluated = {r["time"] for r in accuracy_log}
    now_utc = datetime.now(timezone.utc)
    tz = None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        pass

    # Group unevaluated past slots by date so we query InfluxDB once per day
    from datetime import date as _date_t
    slots_by_date: dict[str, list] = {}
    for key, planned in plan_history.items():
        if key in already_evaluated:
            continue
        try:
            dt = datetime.fromisoformat(key)
            if tz:
                dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
            # Only evaluate completed hours (at least 1h ago)
            if dt.astimezone(timezone.utc) > now_utc - timedelta(hours=1):
                continue
            date_str = dt.date().isoformat()
            slots_by_date.setdefault(date_str, []).append((key, dt.hour, planned))
        except Exception:
            pass

    if not slots_by_date:
        return

    try:
        from influx_writer import query_day_actuals
    except ImportError:
        return

    new_records = []
    for date_str, slots in slots_by_date.items():
        try:
            actuals = query_day_actuals(date_str, tz_name)
        except Exception:
            continue
        for key, hour, planned in slots:
            actual = actuals.get(hour, {})
            if not actual:
                continue
            rec: dict = {"time": key, "date": date_str, "hour": hour}

            # Solar: planned Wh vs actual W (mean over hour = Wh)
            actual_solar = actual.get("solar_w", None)
            if actual_solar is not None and planned["solar_wh"] is not None:
                rec["solar_planned_wh"] = planned["solar_wh"]
                rec["solar_actual_wh"]  = round(actual_solar, 1)  # W·h = Wh for hourly avg
                # bias = planned - actual (positive = over-predicted)
                if actual_solar > 10:  # ignore near-zero (night)
                    rec["solar_bias_pct"] = round(
                        (planned["solar_wh"] - actual_solar) / actual_solar * 100, 1)

            # Consumption: planned Wh vs actual W
            actual_cons = actual.get("house_w", None)
            if actual_cons is not None and planned["consumption_wh"] is not None:
                rec["cons_planned_wh"] = planned["consumption_wh"]
                rec["cons_actual_wh"]  = round(actual_cons, 1)
                rec["cons_bias_pct"]   = round(
                    (planned["consumption_wh"] - actual_cons) / max(actual_cons, 1) * 100, 1)

            # SoC: planned start vs actual
            actual_soc = actual.get("bat_soc", None)
            if actual_soc is not None:
                rec["soc_planned"] = planned["soc_start"]
                rec["soc_actual"]  = round(actual_soc, 1)
                rec["soc_error"]   = round(planned["soc_start"] - actual_soc, 1)

            rec["planned_action"] = planned["action"]
            new_records.append(rec)

    if not new_records:
        return

    accuracy_log.extend(new_records)
    # Prune to last 30 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    accuracy_log = [r for r in accuracy_log if r.get("time", "") >= cutoff]

    try:
        with open(_PLAN_ACCURACY_FILE, "w", encoding="utf-8") as f:
            json.dump(accuracy_log, f)
        log.debug("plan_accuracy: added %d new records (total %d)",
                  len(new_records), len(accuracy_log))
    except Exception as e:
        log.warning("plan_accuracy: write failed: %s", e)


def _get_accuracy_summary() -> dict | None:
    """Compute summary statistics from _plan_accuracy.json for Claude context.
    Returns None if insufficient data (< 20 records).
    """
    try:
        with open(_PLAN_ACCURACY_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        return None

    solar_biases = [r["solar_bias_pct"] for r in records if "solar_bias_pct" in r]
    cons_biases  = [r["cons_bias_pct"]  for r in records if "cons_bias_pct"  in r]
    soc_errors   = [r["soc_error"]      for r in records if "soc_error"      in r]

    if len(solar_biases) + len(cons_biases) + len(soc_errors) < 20:
        return None

    def _stats(values: list) -> dict:
        if not values:
            return {}
        n   = len(values)
        avg = round(sum(values) / n, 1)
        mae = round(sum(abs(v) for v in values) / n, 1)
        return {"avg_bias_pct": avg, "mae_pct": mae, "n": n}

    solar_stats = _stats(solar_biases)
    cons_stats  = _stats(cons_biases)
    soc_mae     = round(sum(abs(e) for e in soc_errors) / len(soc_errors), 1) if soc_errors else None

    # Build human-readable advice
    notes = []
    if solar_stats and abs(solar_stats["avg_bias_pct"]) > 10:
        direction = "te optimistisch" if solar_stats["avg_bias_pct"] > 0 else "te pessimistisch"
        notes.append(
            f"Zonprognose gemiddeld {abs(solar_stats['avg_bias_pct']):.0f}% {direction} "
            f"→ {'plan grid_charge als backup bij bewolkt' if solar_stats['avg_bias_pct'] > 0 else 'solar_charge eerder inzetten'}."
        )
    if cons_stats and abs(cons_stats["avg_bias_pct"]) > 10:
        direction = "onderschat" if cons_stats["avg_bias_pct"] < 0 else "overschat"
        notes.append(
            f"Verbruiksprofiel gemiddeld {abs(cons_stats['avg_bias_pct']):.0f}% {direction} "
            f"→ {'reserveer meer batterijcapaciteit voor verbruik' if cons_stats['avg_bias_pct'] < 0 else 'minder reserve nodig'}."
        )
    if soc_mae and soc_mae > 5:
        notes.append(
            f"SoC-prognose gemiddeld {soc_mae:.0f}% afwijking van werkelijkheid "
            "→ houd ruimere veiligheidsmarge aan bij lage SOC-planning."
        )

    return {
        "records_analysed": len(records),
        "solar_forecast":  solar_stats,
        "consumption_forecast": cons_stats,
        "soc_plan_mae_pct": soc_mae,
        "advice": notes,
    }


# ---------------------------------------------------------------------------
# Last-run debug info (read by app.py after build_plan_claude returns)
# ---------------------------------------------------------------------------

_last_debug: dict = {}


def get_last_debug() -> dict:
    return dict(_last_debug)


def _set_debug(**kwargs):
    _last_debug.clear()
    _last_debug.update(kwargs)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Je bent een financieel optimalisatie-agent voor thuisbatterijen in de Belgische energiemarkt.

## Doel
Minimaliseer de netto elektriciteitskosten over de volledige 48-uur planning.
Analyseer de VOLLEDIGE prijscurve voordat je per uur beslist — lokale beslissingen missen globale kansen.

---

## De 5 acties: exacte definitie en kostenmodel

| Actie | SOC-effect | Financieel effect |
|---|---|---|
| `grid_charge` | +max_charge_kw × rte kWh (max tot max_soc_pct) | Kost: buy_price/rte + depreciation per opgeslagen kWh |
| `solar_charge` | +(net_wh/1000) × rte kWh (max tot max_soc_pct) | Gratis: zon anders verloren/teruggeleverd |
| `discharge` | −min(consumption_wh/1000, bat_kwh − reserve_kwh) | Besparing: buy_price × discharged_kwh (net niet nodig) |
| `save` | ±0: batterij volledig bevroren (hardware stop) | Kost: buy_price × consumption_wh/1000 (net dekt alles) |
| `neutral` | net_wh > 0 → +surplus×rte; net_wh < 0 → −min(net_wh/1000, bat_boven_reserve) | Zon laadt op; 's nachts draint batterij voor verbruik |

### Sleutelonderscheid: save vs neutral
- **`neutral` overdag (net_wh > 0)**: zonne-overschot laadt batterij gratis op — goed.
- **`neutral` 's nachts (net_wh < 0)**: batterij ontlaadt voor verbruik — SOC daalt met |net_wh|/capacity_kwh × 100% per uur.
- **`save`**: SOC volledig bevroren. Het net dekt ALLES inclusief verbruik — kost buy_price × verbruik per uur.
- **Wanneer is save goedkoper dan neutral?** Alleen als de bewaarde SOC later meer oplevert dan de save-kosten:
  `save_benefit = discharge_price_later × bewaarde_kwh − buy_price × verbruik_nu × save_uren`
  → Positief = save loont. Negatief = save kost netto geld vs neutral.
- **NOOIT save als SOC ≤ reserve + 5%**: er is dan < 0.5 kWh te bewaren — save-kosten zijn altijd groter dan baat.

---

## Break-even formule (staat in elk slot als `breakeven_eur_kwh`)

```
breakeven = buy_price_nu / rte + depreciation_eur_kwh
```

`grid_charge` is alleen winstgevend als er later een slot bestaat met `buy_price > breakeven_nu`.
Elk slot bevat zijn eigen `breakeven_eur_kwh` en `grid_charge_potential_eur_kwh` (= max toekomstige prijs − breakeven, 0 als verlieslatend).

---

## Beslissingsalgoritme: doe dit in volgorde

### Stap 1 — Globale analyse van de 48-uur curve (altijd eerst)

a) Sorteer alle slots op prijs. Identificeer:
   - Goedkope slots: prijs ≤ p25 of negatief → grid_charge kandidaten
   - Dure slots: prijs ≥ p75 → discharge kandidaten
   - Spaarvensters: slots net vóór een duur slot (ALLEEN als duur slot ≤ 3 uur weg)

b) Voor elk goedkoop slot: controleer of `grid_charge_potential_eur_kwh > 0`.
   → Zo ja: markeer als grid_charge (netlaadkandidaat), mits SOC < max.

c) Bereken verwachte SOC bij zonsopgang (06:00) na een neutrale nacht:
   `soc_06u = huidig_soc − (standby_w / capacity_kwh / 1000 × 100) × uren_tot_06u`
   → Als soc_06u < reserve + 10% én ochtend heeft dure uren → nacht grid_charge nodig.

d) "Dubbele winst"-kansen: slot met prijs ≥ p75 nu + goedkoop nachtslot later + duur ochtendslot?
   → Discharge NU + grid_charge nacht + discharge ochtend = maximale winst.

### Stap 2 — SOC-simulatie door je plan

Simuleer de SOC chronologisch met je geplande acties:
```
grid_charge: bat = min(bat_max, bat + max_charge_kw × rte)
solar_charge: bat = min(bat_max, bat + net_wh/1000 × rte)
discharge:    bat = max(bat_min, bat − min(consumption_wh/1000, bat − bat_min))
save:         bat ongewijzigd
neutral:      als net_wh >= 0: bat = min(bat_max, bat + net_wh/1000 × rte)
              als net_wh < 0:  bat = max(bat_min, bat + net_wh/1000)
```
⚠️ De `soc_start_pct` in de invoer is een neutral-baseline (alsof alles neutral is). Zodra jij
andere acties kiest, wijkt de werkelijke SOC af. **Gebruik je eigen simulatie.**

### Stap 3 — Prioriteitsvolgorde per slot (pas toe na stap 1+2)

Evalueer elk slot in deze exacte volgorde — eerste toepasselijke regel wint:

1. **buy_price < 0** → `grid_charge` (betaald om te laden — altijd doen)
2. **net_wh > 200 EN SOC < max EN buy_price ≥ 0.02** → `solar_charge` (gratis zonne-energie)
3. **grid_charge_potential_eur_kwh > 0 EN SOC < max** → `grid_charge` (winstgevend laden)
4. **buy_price ≥ p75 EN gesimuleerde SOC > reserve + 10%** → `discharge` (duur uur: gebruik batterij)
   - Uitzondering: er is een slot BINNEN 2 UUR met prijs > huidige × 1.10 → wacht dat slot af.
   - **Discharge WINT altijd van save** als buy_price ≥ p75 — dit is een harde override.
5. **Discharge-slot binnen 3 uur EN buy_price < p75 EN gesimuleerde SOC > reserve + 5% EN save_benefit > 0**
   → `save` (bewaar lading voor nabij duur slot)
6. **Anders** → `neutral` (standaard — goed voor overdag met zon; 's nachts draint batterij)

### Stap 4 — Conflicten oplossen

- `grid_charge` maar gesimuleerde SOC ≥ max → `solar_charge` (als net_wh > 200) anders `neutral`
- `discharge` maar gesimuleerde SOC ≤ reserve → `save` (NIET neutral — neutral draint verder!)
- `save` maar gesimuleerde SOC ≤ reserve + 5% → `neutral` (niets te bewaren)
- `save` > 3 opeenvolgende nachtturen EN morgen > 6 kWh zon EN geen ochtendpiek → `neutral`
  (de zon herlaadt toch; save kost je alleen de nachtrekening)

---

## Netladen (`grid_charge`): wanneer loont het?

`grid_charge_potential_eur_kwh` in elk slot vertelt je direct of laden winstgevend is.
- **> 0**: laden loont — er bestaat een toekomstig slot met voldoende hogere prijs.
- **= 0**: laden is verlieslatend of break-even — gebruik `neutral` of `solar_charge`.
- **Negatieve prijs**: altijd laden, ook als geen toekomstig duur slot bestaat (je wordt betaald).

Netladen is in België alleen winstgevend bij grote prijsverschillen (typisch > 5ct/kWh spread na RTE + afschrijving).
Verwacht dit niet elke dag — op vlakke dagen (prijzen 14–18ct) is solar_charge + discharge de enige winstgevende strategie.

---

## Sparen (`save`): berekening of het loont

Save kost geld per uur: `save_cost_eur = buy_price × consumption_wh / 1000`
Save levert later op: `save_gain_eur = discharge_price_straks × bewaarde_kwh`

Gebruik save alleen als `save_gain > save_cost × save_uren`.

**Vuistregels:**
- Save 1 uur vóór een duur slot van 2ct meerprijs: loon = 2ct × 0.3 kWh = 0.006 € → nauwelijks de moeite.
- Save 3 uur vóór een piek van 8ct meerprijs: loon = 8ct × 0.9 kWh − 3 × 16ct × 0.3 kWh = 0.072 − 0.144 = −0.07 € → verlies!
- Conclusie: save loont alleen bij GROTE prijsverschillen (≥ 5ct) EN SHORT windows (≤ 2 uur).

---

## Nachtverbruik en zonsopgang-SOC

Sluipverbruik per nachtuur (bij `neutral`):
```
drain_pct_per_uur = standby_w / (capacity_kwh × 1000) × 100
```
Voorbeeld: 456W standby, 10 kWh → 4.56% per uur → 8u nacht = 36% drain.

Als voorspelde SOC bij 06:00 < reserve + 10% EN ochtendprijzen ≥ p75:
→ Grid_charge in de 1–2 goedkoopste nachturen (mits `grid_charge_potential_eur_kwh > 0`).

Als morgen > 6 kWh zonverwachting (zichtbaar in solar_wh slots): de zon herlaadt de batterij toch
ongeacht het exacte SOC-startniveau bij zonsopgang. Dan is `neutral` 's nachts bijna altijd beter dan `save`.

---

## Historische context

Als `historical_context` aanwezig is in de invoer, gebruik dit actief:

**historical_context.soc**: werkelijk gemeten SOC-profiel per weekdag × uur.
- Typisch laag uur → anticipeer met grid_charge nacht ervoor.
- Typisch hoog uur (veel zon) → grid_charge die nacht niet nodig.

**historical_context.plan_accuracy.advice**: volg de concrete aanbevelingen op.
- Zonprognose structureel optimistisch → extra grid_charge als backup bij bewolkt.
- Verbruik structureel onderschat → minder agressief discharge.

**historical_context.price**: detecteer of huidige dag goedkoop/duur is t.o.v. historisch gemiddelde.
- Prijs > 20% onder historisch → extra grid_charge kans.
- Vermeld in je reason: "hist. SOC za 14u gem. 85% → geen grid_charge nodig"

---

## Harde constraints (nooit overtreden)

| Constraint | Gevolg |
|---|---|
| SOC ≤ reserve | NOOIT discharge of neutral draining |
| SOC ≥ max_soc | NOOIT grid_charge |
| net_wh ≤ 0 | NOOIT solar_charge |
| buy_price < 0.02 én solar_charge gewenst | Gebruik grid_charge i.p.v. solar_charge |
| SOC ≤ reserve + 5% | NOOIT save (niets te bewaren) |
| buy_price ≥ p75 EN SOC > reserve + 10% | ALTIJD discharge — nooit save of neutral |
| grid_charge_potential_eur_kwh = 0 | NOOIT grid_charge (verlieslatend) |
| Meer dan 4 opeenvolgende save-uren | Herbekijk: is er tussenin goedkope nachtlading? Zo ja: discharge + grid_charge + discharge |

---

## Antwoord
Gebruik de `submit_battery_plan` tool. Per slot: `time` (exact kopiëren), `action`, `reason` (max 80 tekens).
Vermeld in reason: prijs vs breakeven, SOC, en waarom deze actie (bijv. "16ct > breakeven 14ct → grid_charge").
Geef voor elk slot precies één actie. Sla geen slots over.
"""


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def build_plan_claude(
    prices: list[dict],
    solar_wh: dict[str, float],
    consumption_by_hour: list[dict],
    bat_soc_now: float,
    settings: Optional[dict] = None,
    start_dt: Optional[datetime] = None,
    num_slots: int = 48,
    today_actuals: Optional[dict] = None,
) -> list[dict]:
    """
    Build an hourly battery plan using the Claude AI API.
    Returns slots in the same format as strategy.build_plan().
    Falls back to rule-based engine on any error.
    Debug info is stored in _last_debug (read via get_last_debug()).
    """
    from strategy import (build_plan, load_strategy_settings,
                          SOLAR_CHARGE, GRID_CHARGE, SAVE, DISCHARGE, NEUTRAL)

    s = settings or load_strategy_settings()

    api_key = s.get("claude_api_key", "").strip()
    if not api_key:
        log.warning("strategy_claude: no API key configured — falling back to rule-based")
        _set_debug(fallback=True, fallback_reason="Geen API-sleutel geconfigureerd")
        return build_plan(prices, solar_wh, consumption_by_hour, bat_soc_now, s,
                          start_dt, num_slots)

    try:
        import anthropic
    except ImportError:
        log.error("strategy_claude: 'anthropic' package not installed — falling back")
        _set_debug(fallback=True, fallback_reason="'anthropic' package niet geïnstalleerd")
        return build_plan(prices, solar_wh, consumption_by_hour, bat_soc_now, s,
                          start_dt, num_slots)

    cap_kwh       = float(s["bat_capacity_kwh"])
    rte           = float(s["rte"])
    depr          = float(s["depreciation_eur_kwh"])
    min_soc_f     = float(s["min_reserve_soc"]) / 100.0
    max_soc_f     = float(s["max_soc"]) / 100.0
    max_charge_kw = float(s["max_charge_kw"])
    # Frank Energie prices are already all-in (include all taxes + sourcing markup).
    # Adding grid_markup_eur_kwh on top would double-count the energy taxes.
    # Only add markup for ENTSO-E prices (which are raw wholesale market prices).
    price_source_used = s.get("price_source", "entsoe")
    if price_source_used == "frank":
        markup = 0.0
    else:
        markup = float(s.get("grid_markup_eur_kwh", 0.133))
    grid_components = {
        "afnametarief_ct":       float(s.get("grid_afname_ct",           5.75)),
        "bijzondere_accijns_ct": float(s.get("grid_accijns_ct",          5.03)),
        "gsc_ct":                float(s.get("grid_gsc_ct",              1.17)),
        "wkk_ct":                float(s.get("grid_wkk_ct",              0.37)),
        "energiebijdrage_ct":    float(s.get("grid_energiebijdrage_ct",  0.20)),
        "btw_pct":               float(s.get("grid_btw_pct",             6.0)),
    }
    tz_name       = s.get("timezone", "Europe/Brussels")
    tz            = ZoneInfo(tz_name)
    model         = s.get("claude_model", "claude-haiku-4-5-20251001")

    bat_min = min_soc_f * cap_kwh
    bat_max = max_soc_f * cap_kwh

    # ── Price lookup ──────────────────────────────────────────────────────
    price_by_slot: dict[str, list] = {}
    for p in prices:
        try:
            dt_raw = datetime.fromisoformat(p["from"])
            dt_loc = (dt_raw.replace(tzinfo=tz) if dt_raw.tzinfo is None
                      else dt_raw.astimezone(tz))
            key = dt_loc.replace(minute=0, second=0, microsecond=0).isoformat()
            price_by_slot.setdefault(key, []).append(float(p["marketPrice"]))
        except Exception:
            pass
    price_slots: dict[str, float] = {
        k: sum(v) / len(v) for k, v in price_by_slot.items()
    }

    # ── Solar lookup ──────────────────────────────────────────────────────
    solar_by_slot: dict[str, float] = {}
    for k, wh in (solar_wh or {}).items():
        try:
            dt_str = k if "T" in k else k.replace(" ", "T")
            dt = datetime.fromisoformat(dt_str)
            dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
            key = dt.replace(minute=0, second=0, microsecond=0).isoformat()
            solar_by_slot[key] = solar_by_slot.get(key, 0.0) + float(wh)
        except Exception:
            pass

    # ── Consumption lookup (weekday-aware) ────────────────────────────────
    cons_by_wd_hour: dict[tuple, float] = {}
    cons_by_hour:    dict[int, float]   = {}
    for x in (consumption_by_hour or []):
        h = int(x["hour"]); v = float(x["avg_wh"]); wd = x.get("weekday")
        if wd is not None:
            cons_by_wd_hour[(int(wd), h)] = v
        else:
            cons_by_hour[h] = v
    has_wd = bool(cons_by_wd_hour)

    def _cons(wd: int, h: int) -> float:
        if has_wd:
            return cons_by_wd_hour.get((wd, h), cons_by_hour.get(h, 300.0))
        return cons_by_hour.get(h, 300.0)

    # ── Hourly window ─────────────────────────────────────────────────────
    real_now = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    if start_dt is not None:
        now_local = start_dt.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    else:
        now_local = real_now.replace(hour=0, minute=0, second=0, microsecond=0)
    all_slots = [now_local + timedelta(hours=i) for i in range(num_slots)]

    # ── Record price history + build weekly profiles ──────────────────────
    _record_price_history(price_slots, markup)
    _price_history   = _load_price_history()
    _weekly_profile  = _compute_weekly_profile(_price_history)
    _history_days    = len(_price_history)

    _soc_profile, _soc_history_days = _get_soc_profile(tz_name)

    # Evaluate past plans against actuals (best-effort, non-blocking)
    try:
        _evaluate_past_plans(tz_name)
    except Exception as _eval_exc:
        log.debug("plan_accuracy: evaluation failed (non-fatal): %s", _eval_exc)
    _accuracy_summary = _get_accuracy_summary()

    # Price statistics (all-in prices)
    known_prices = [
        price_slots[sl.isoformat()] + markup
        for sl in all_slots
        if sl.isoformat() in price_slots
    ]
    if known_prices:
        sp = sorted(known_prices); n = len(sp)
        p25    = sp[int(n * 0.25)]
        median = sp[n // 2]
        p75    = sp[int(n * 0.75)]
        p_min  = sp[0]
        p_max  = sp[-1]
    else:
        p25 = median = p75 = p_min = p_max = 0.10

    # Break-even price for grid charging
    breakeven = round(p_min / rte + depr, 4) if known_prices else None

    # ── Build input slots for Claude ──────────────────────────────────────
    # Simulate a realistic SOC baseline assuming "neutral" for all future slots:
    # neutral = anti-feed = battery drains for consumption load when no solar.
    # This gives Claude a realistic picture of how the SOC evolves overnight
    # instead of a flat line that suggests the battery never drains.
    slots_input = []
    _bat_sim = bat_soc_now / 100.0 * cap_kwh

    # Pre-collect all future buy prices (ordered) for grid_charge_potential calculation
    _future_prices: list[tuple[int, float]] = []  # (index, buy_price)
    for _i, _sd in enumerate(all_slots):
        _r = price_slots.get(_sd.isoformat())
        if _r is not None:
            _future_prices.append((_i, _r + markup))

    for i, slot_dt in enumerate(all_slots):
        key   = slot_dt.isoformat()
        raw   = price_slots.get(key)
        buy   = (raw + markup) if raw is not None else None

        # For past hours: use today's InfluxDB actuals instead of forecast/historical
        is_past = slot_dt < real_now
        actual_row = (today_actuals or {}).get(slot_dt.hour, {}) if is_past else {}
        actual_solar = actual_row.get("solar_w")   # W·h ≈ Wh for 1h average
        actual_cons  = actual_row.get("house_w")

        solar = round(actual_solar if actual_solar is not None else solar_by_slot.get(key, 0.0))
        cons  = round(actual_cons  if actual_cons  is not None else _cons(slot_dt.weekday(), slot_dt.hour))
        net   = solar - cons

        # Per-slot break-even: minimum future discharge price to make this charge profitable
        slot_be = round(buy / rte + depr, 4) if buy is not None else None

        # Pre-calculate grid_charge_potential: max(0, best future price - this slot's breakeven)
        # Positive = charging now and discharging later is profitable.
        # Zero = charging now is break-even or loss-making.
        if buy is not None and slot_be is not None:
            best_future = max(
                (fp for fi, fp in _future_prices if fi > i),
                default=0.0,
            )
            gc_potential = round(max(0.0, best_future - slot_be), 4)
        else:
            gc_potential = None

        slot_entry: dict = {
            "time":                        key,
            "weekday":                     WEEKDAY_NL[slot_dt.weekday()],
            "hour":                        slot_dt.hour,
            "buy_price_eur_kwh":           round(buy, 4) if buy is not None else None,
            "breakeven_eur_kwh":           slot_be,
            "grid_charge_potential_eur_kwh": gc_potential,  # >0 = winstgevend laden
            "solar_wh":                    solar,
            "consumption_wh":              cons,
            "net_wh":                      net,
            "soc_start_pct":               round((_bat_sim / cap_kwh) * 100, 1),
            "is_past":                     is_past,
        }
        if is_past and (actual_solar is not None or actual_cons is not None):
            slot_entry["used_actual"] = True
        slots_input.append(slot_entry)

        # Advance simulated SOC assuming neutral (battery covers net consumption)
        if slot_dt >= real_now:
            if net >= 0:
                # Solar surplus: battery charges (up to max)
                _bat_sim = min(bat_max, _bat_sim + (net / 1000.0) * rte)
            else:
                # Consumption exceeds solar: battery drains (down to reserve)
                _bat_sim = max(bat_min, _bat_sim + net / 1000.0)

    # ── Vandaag-tot-nu samenvatting voor Claude ───────────────────────────
    vandaag_tot_nu: dict = {}
    if today_actuals:
        past_hours = sorted(h for h in today_actuals if h < real_now.hour)
        if past_hours:
            tot_solar = sum(today_actuals[h].get("solar_w", 0.0) for h in past_hours)
            tot_house = sum(today_actuals[h].get("house_w", 0.0) for h in past_hours)
            tot_net   = sum(today_actuals[h].get("net_w",  0.0) for h in past_hours)
            vandaag_tot_nu = {
                "gemeten_uren":      past_hours,
                "totaal_zon_wh":     round(tot_solar),
                "totaal_verbruik_wh": round(tot_house),
                "totaal_net_wh":     round(tot_net),
                "note": (
                    f"Werkelijke InfluxDB-metingen vandaag uur {past_hours[0]:02d}:00–{past_hours[-1]+1:02d}:00. "
                    "Zonwaarden in de slots voor deze uren zijn al vervangen door de werkelijke meting. "
                    "Gebruik dit om te beoordelen of de dag zonniger/bewolkter was dan verwacht."
                ),
            }

    # ── Build Claude request payload ──────────────────────────────────────
    payload = {
        "battery": {
            "capacity_kwh":         cap_kwh,
            "current_soc_pct":      round(bat_soc_now, 1),
            "min_reserve_soc_pct":  float(s["min_reserve_soc"]),
            "max_soc_pct":          float(s["max_soc"]),
            "max_charge_kw":        max_charge_kw,
            "rte":                  rte,
            "depreciation_eur_kwh": depr,
            "grid_markup_eur_kwh":  markup,
            "grid_markup_components": grid_components,
            "breakeven_grid_charge_eur_kwh": breakeven,
            "standby_w": float(s.get("standby_w", 0)),  # baseline night consumption (02-06h avg)
        },
        "price_stats": {
            "p25_eur_kwh":    round(p25,    4),
            "median_eur_kwh": round(median, 4),
            "p75_eur_kwh":    round(p75,    4),
            "min_eur_kwh":    round(p_min,  4),
            "max_eur_kwh":    round(p_max,  4),
            "note": (
                f"All-in prijzen (marktprijs + {markup*100:.0f}ct nettarief). "
                f"Elke slot heeft zijn eigen breakeven_eur_kwh = buy_price/rte+depreciation. "
                f"Laagste breakeven in deze periode: {breakeven*100:.1f}ct/kWh."
                if breakeven else "Geen prijzen beschikbaar."
            ),
        },
        "slots": slots_input,
    }

    # Attach historical context when we have at least 3 days of data
    historical_context: dict = {}
    if _weekly_profile and _history_days >= 3:
        historical_context["price"] = {
            "days_of_history": _history_days,
            "note": (
                f"Historisch prijsprofiel op basis van {_history_days} dagen data. "
                "Elk item: weekdag, uur, avg/p25/p75 all-in prijs (€/kWh). "
                "Gebruik dit om te detecteren of het huidige uur goedkoop of duur is t.o.v. historisch patroon."
            ),
            "weekly_price_profile": _weekly_profile,
        }
    if _soc_profile and _soc_history_days >= 3:
        historical_context["soc"] = {
            "days_of_history": _soc_history_days,
            "note": (
                f"Werkelijk gemeten SoC-profiel op basis van {_soc_history_days} dagen InfluxDB-data. "
                "Elk item: weekdag, uur, avg/p25/p75 van de werkelijke batterijlading (%). "
                "Gebruik dit om te anticiperen: als de SoC historisch laag is op een bepaald uur "
                "(bijv. maandagochtend 07:00 typisch 25%), plan dan grid_charge de nacht ervoor. "
                "Als de SoC historisch hoog is, is extra grid_charge waarschijnlijk niet nodig."
            ),
            "weekly_soc_profile": _soc_profile,
        }
    if _accuracy_summary:
        historical_context["plan_accuracy"] = {
            "note": (
                "Vergelijking van vorige plannen met werkelijke InfluxDB-metingen. "
                "Gebruik 'advice' om structurele afwijkingen te compenseren in je plan."
            ),
            **_accuracy_summary,
        }
    if historical_context:
        payload["historical_context"] = historical_context

    if vandaag_tot_nu:
        payload["vandaag_tot_nu"] = vandaag_tot_nu

    tool_def = {
        "name": "submit_battery_plan",
        "description": "Geef het volledige uurlijks batterijplan terug voor alle slots in de invoer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "array",
                    "description": "Eén entry per slot, in dezelfde volgorde als de invoer.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {
                                "type":        "string",
                                "description": "Exact dezelfde ISO-tijdstempel als in de invoer 'time'.",
                            },
                            "action": {
                                "type": "string",
                                "enum": ["solar_charge", "grid_charge", "save", "discharge", "neutral"],
                            },
                            "reason": {
                                "type":        "string",
                                "description": "Korte Nederlandse motivatie (max 80 tekens).",
                            },
                        },
                        "required": ["time", "action", "reason"],
                    },
                },
            },
            "required": ["plan"],
        },
    }

    log.info("strategy_claude: calling model=%s  slots=%d  breakeven=%.3f",
             model, len(slots_input), breakeven or 0)

    t0 = _time.monotonic()
    try:
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0.1,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=[tool_def],
            tool_choice={"type": "tool", "name": "submit_battery_plan"},
            messages=[{
                "role":    "user",
                "content": (
                    "Hier zijn de batterijparameters en de geplande uren voor de komende 48 uur. "
                    "Analyseer de prijscurve en stel het optimale laadplan op:\n\n"
                    f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
                ),
            }],
        )
    except Exception as exc:
        elapsed = round(_time.monotonic() - t0, 2)
        log.error("strategy_claude: API call failed (%.1fs): %s — falling back", elapsed, exc)
        _set_debug(
            fallback=True,
            fallback_reason=f"API-fout: {exc}",
            model=model,
            elapsed_s=elapsed,
        )
        return build_plan(prices, solar_wh, consumption_by_hour, bat_soc_now, s,
                          start_dt, num_slots)

    elapsed = round(_time.monotonic() - t0, 2)

    # ── Parse tool-use response ───────────────────────────────────────────
    VALID_ACTIONS = {SOLAR_CHARGE, GRID_CHARGE, SAVE, DISCHARGE, NEUTRAL}
    plan_actions: dict[str, tuple[str, str]] = {}
    raw_plan_items: list = []

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_battery_plan":
            raw_plan_items = block.input.get("plan", [])
            for item in raw_plan_items:
                t = str(item.get("time", "")).strip()
                a = str(item.get("action", "neutral"))
                r = str(item.get("reason", ""))
                if a not in VALID_ACTIONS:
                    a = NEUTRAL
                plan_actions[t] = (a, r)
            break

    # ── Post-processing: failsafe overrides ──────────────────────────────
    # Rule 1: save + solar surplus + battery not full → solar_charge
    # save freezes the hardware completely; solar energy would be lost.
    _override_count = 0
    for si in slots_input:
        key = si["time"]
        if key not in plan_actions:
            continue
        action, reason = plan_actions[key]
        if action == SAVE:
            net_wh  = si.get("net_wh", 0)
            soc_pct = si.get("soc_start_pct", 100)
            if net_wh > 200 and soc_pct < float(s["max_soc"]) - 1:
                plan_actions[key] = (
                    SOLAR_CHARGE,
                    f"[auto] save→solar_charge: {net_wh}Wh overschot, SOC {soc_pct}% niet vol",
                )
                _override_count += 1
                log.debug("post-process: %s save→solar_charge (net=%dWh soc=%.1f%%)",
                          key, net_wh, soc_pct)

    if _override_count:
        log.info("strategy_claude: post-process overrode %d save→solar_charge (solar failsafe)",
                 _override_count)

    # ── Feasibility pass: SOC forward-simulation to catch impossible actions ─
    # Claude's SOC baseline in the prompt was a neutral-mode estimate; actual
    # planned actions shift SOC differently. This corrects the most dangerous
    # mismatches before execution.
    _feasibility_overrides = 0
    _feas_bat = bat_soc_now / 100.0 * cap_kwh
    for slot_dt in all_slots:
        key = slot_dt.isoformat()
        if key not in plan_actions:
            continue
        action, reason = plan_actions[key]
        solar_f = solar_by_slot.get(key, 0.0)
        cons_f  = _cons(slot_dt.weekday(), slot_dt.hour)
        net_f   = solar_f - cons_f

        # Discharge impossible → save (prevents battery from going below reserve)
        if action == DISCHARGE and _feas_bat <= bat_min + 0.2:
            fixed = (SAVE, f"[feasibility] discharge→save: SOC {_feas_bat / cap_kwh * 100:.0f}% ≤ reserve")
            plan_actions[key] = fixed
            action = SAVE
            _feasibility_overrides += 1
            log.debug("feasibility: %s discharge→save (bat=%.2fkWh)", key, _feas_bat)

        # Grid-charge when already full → neutral
        elif action == GRID_CHARGE and _feas_bat >= bat_max - 0.05:
            fixed = (NEUTRAL, f"[feasibility] grid_charge→neutral: SOC al {_feas_bat / cap_kwh * 100:.0f}%")
            plan_actions[key] = fixed
            action = NEUTRAL
            _feasibility_overrides += 1
            log.debug("feasibility: %s grid_charge→neutral (bat=%.2fkWh)", key, _feas_bat)

        # Save with virtually empty battery → neutral
        # System prompt rule: ❌ save when soc < reserve + 5% — nothing meaningful to preserve.
        # This also catches the "save all night at 16% SOC" pattern: with only 1% above reserve
        # there is no charge worth preserving, and save costs money (grid covers all consumption).
        elif action == SAVE and _feas_bat <= bat_min + cap_kwh * 0.05:
            fixed = (NEUTRAL, f"[feasibility] save→neutral: SOC {_feas_bat / cap_kwh * 100:.0f}% ≤ reserve+5%, niets te bewaren")
            plan_actions[key] = fixed
            action = NEUTRAL
            _feasibility_overrides += 1
            log.debug("feasibility: %s save→neutral (bat=%.2fkWh ≤ min+5%%)", key, _feas_bat)

        # Advance simulated SOC for next iteration
        if action == GRID_CHARGE:
            headroom = bat_max - _feas_bat
            draw_kw  = min(max_charge_kw, headroom / rte if rte > 0 else 0)
            if draw_kw > 0.05:
                _feas_bat = min(bat_max, _feas_bat + draw_kw * rte)
        elif action == SOLAR_CHARGE:
            if net_f > 0:
                _feas_bat = min(bat_max, _feas_bat + (net_f / 1000.0) * rte)
        elif action == DISCHARGE:
            avail = _feas_bat - bat_min
            use   = min(cons_f / 1000.0, avail)
            if use > 0.05:
                _feas_bat -= use
        elif action == NEUTRAL:
            if net_f >= 0:
                _feas_bat = min(bat_max, _feas_bat + (net_f / 1000.0) * rte)
            else:
                avail = _feas_bat - bat_min
                use   = min((-net_f) / 1000.0, avail)
                if use > 0:
                    _feas_bat -= use
        # SAVE: SOC unchanged

    if _feasibility_overrides:
        log.info("strategy_claude: feasibility pass overrode %d actions", _feasibility_overrides)

    if not plan_actions:
        log.warning("strategy_claude: no valid tool_use in response — falling back (%.1fs)", elapsed)
        _set_debug(
            fallback=True,
            fallback_reason="Geen geldige tool_use in antwoord",
            model=model,
            elapsed_s=elapsed,
            input_tokens=getattr(response.usage, "input_tokens", None),
            output_tokens=getattr(response.usage, "output_tokens", None),
            stop_reason=getattr(response, "stop_reason", None),
        )
        return build_plan(prices, solar_wh, consumption_by_hour, bat_soc_now, s,
                          start_dt, num_slots)

    # Count actions for summary
    action_counts: dict[str, int] = {}
    for a, _ in plan_actions.values():
        action_counts[a] = action_counts.get(a, 0) + 1

    log.info("strategy_claude: received %d actions in %.1fs  in=%s out=%s",
             len(plan_actions), elapsed,
             getattr(response.usage, "input_tokens", "?"),
             getattr(response.usage, "output_tokens", "?"))

    # ── Reconstruct slot list with SOC simulation ─────────────────────────
    bat_kwh      = bat_soc_now / 100.0 * cap_kwh
    result_slots = []

    for slot_dt in all_slots:
        key    = slot_dt.isoformat()
        raw    = price_slots.get(key)
        buy    = (raw + markup) if raw is not None else None
        solar  = solar_by_slot.get(key, 0.0)
        cons   = _cons(slot_dt.weekday(), slot_dt.hour)
        net    = solar - cons

        action, reason = plan_actions.get(key, (NEUTRAL, "Geen actie van Claude"))
        soc_start = (bat_kwh / cap_kwh) * 100.0

        charge_kwh    = 0.0
        discharge_kwh = 0.0

        if action == GRID_CHARGE:
            headroom       = bat_max - bat_kwh
            charge_draw_kw = min(max_charge_kw, headroom / rte if rte > 0 else 0)
            if charge_draw_kw > 0.05:
                energy_in  = charge_draw_kw * rte
                bat_kwh    = min(bat_max, bat_kwh + energy_in)
                charge_kwh = charge_draw_kw

        elif action == SOLAR_CHARGE:
            if net > 0:
                surplus_kwh = (net / 1000.0) * rte
                headroom    = bat_max - bat_kwh
                store       = min(surplus_kwh, headroom)
                if store > 0:
                    bat_kwh    += store
                    charge_kwh  = net / 1000.0

        elif action == DISCHARGE:
            avail = bat_kwh - bat_min
            use   = min(cons / 1000.0, avail)
            if use > 0.05:
                bat_kwh       -= use
                discharge_kwh  = use

        elif action == NEUTRAL:
            # anti-feed: battery covers net consumption load (no solar at night = drains)
            if net >= 0:
                surplus_kwh = (net / 1000.0) * rte
                headroom    = bat_max - bat_kwh
                store       = min(surplus_kwh, headroom)
                if store > 0:
                    bat_kwh    += store
                    charge_kwh  = net / 1000.0
            else:
                avail = bat_kwh - bat_min
                use   = min((-net) / 1000.0, avail)
                if use > 0:
                    bat_kwh       -= use
                    discharge_kwh  = use

        # SAVE: battery completely passive — no bat_kwh change

        soc_end = (bat_kwh / cap_kwh) * 100.0

        result_slots.append({
            "time":           key,
            "hour":           slot_dt.hour,
            "price_eur_kwh":  round(buy, 4)  if buy is not None else None,
            "price_raw":      round(raw, 4)  if raw is not None else None,
            "solar_wh":       round(solar, 0),
            "consumption_wh": round(cons, 0),
            "net_wh":         round(net, 0),
            "action":         action,
            "reason":         reason,
            "charge_kwh":     round(charge_kwh, 3),
            "discharge_kwh":  round(discharge_kwh, 3),
            "soc_start":      round(soc_start, 1),
            "soc_end":        round(soc_end, 1),
            "is_peak":        False,
            "is_past":        slot_dt < real_now,
        })

    # ── Store debug info ──────────────────────────────────────────────────
    _in_tok          = getattr(response.usage, "input_tokens",          0) or 0
    _out_tok         = getattr(response.usage, "output_tokens",         0) or 0
    _cache_write_tok = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
    _cache_read_tok  = getattr(response.usage, "cache_read_input_tokens",     0) or 0
    _eur     = _token_cost_eur(model, _in_tok, _out_tok, _cache_write_tok, _cache_read_tok)
    _ran_at  = datetime.now(timezone.utc).isoformat()
    _append_usage(_ran_at, model, _in_tok, _out_tok, _eur)

    # ── Persist plan for future accuracy comparison ───────────────────────
    try:
        _save_plan_for_accuracy(result_slots, _ran_at)
    except Exception as _spe:
        log.debug("plan_history: save failed (non-fatal): %s", _spe)

    _set_debug(
        fallback=False,
        model=model,
        ran_at=_ran_at,
        elapsed_s=elapsed,
        input_tokens=_in_tok,
        output_tokens=_out_tok,
        cache_write_tokens=_cache_write_tok,
        cache_read_tokens=_cache_read_tok,
        cost_eur=round(_eur, 6),
        stop_reason=getattr(response, "stop_reason", None),
        slots_sent=len(slots_input),
        slots_received=len(plan_actions),
        action_counts=action_counts,
        breakeven_eur_kwh=breakeven,
        price_history_days=_history_days,
        soc_history_days=_soc_history_days,
        post_process_overrides=_override_count,
        feasibility_overrides=_feasibility_overrides,
        price_stats={
            "p25":    round(p25,    4),
            "median": round(median, 4),
            "p75":    round(p75,    4),
            "min":    round(p_min,  4),
            "max":    round(p_max,  4),
        },
        # Full per-slot reasoning for the debug panel (time, action, reason only)
        slot_reasoning=[
            {"time": item.get("time"), "action": item.get("action"), "reason": item.get("reason")}
            for item in raw_plan_items
        ],
    )

    return result_slots
