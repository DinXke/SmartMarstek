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

# Haiku 4.5 pricing (USD per token, converted to EUR at 0.92)
_PRICE_IN_EUR_PER_TOKEN  = 0.80 / 1_000_000 * 0.92   # $0.80/MTok input
_PRICE_OUT_EUR_PER_TOKEN = 4.00 / 1_000_000 * 0.92   # $4.00/MTok output

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

_SYSTEM_PROMPT = """Je bent een gespecialiseerde optimalisatie-agent voor thuisbatterijen in de Belgische energiemarkt.

## Doel
Minimaliseer de totale elektriciteitskosten over de volledige 48-uur planning. Optimaliseer GLOBAAL — kijk naar de hele curve voordat je per uur beslist.

---

## Acties — exacte definitie

| Actie | Batterijgedrag | SOC-effect |
|---|---|---|
| `grid_charge` | Laadt actief vanuit het net | +max_charge_kw × rte kWh, max tot max_soc_pct |
| `solar_charge` | Laadt uitsluitend van zonne-overschot | +(net_wh/1000) × rte, max tot max_soc_pct |
| `discharge` | Levert actief energie aan het huis | −min(consumption_wh/1000, soc_kwh − reserve_kwh) |
| `save` | **Batterij volledig passief (hardware stop)** | ±0 — SOC bevroren, net levert alles |
| `neutral` | Anti-feed firmware: dekt huisverbruik als er geen zon is | net_wh > 0 → +surplus×rte; net_wh < 0 → −deficit (min. reserve) |

### Cruciaal onderscheid save vs neutral
- `save` = SOC bevroren, nul effect op batterij. Het net dekt alles. Kies save als je lading wil **bewaren** voor een duurder uur.
- `neutral` = batterij ontlaadt actief wanneer solar < verbruik. 's Nachts daalt de SOC elk uur met ≈ consumption_wh / capacity_kwh × 100%.
- **Zeg nooit `neutral` als je bedoelt "bewaar lading voor later" — dat is `save`.**

---

## Kernformule: break-even per slot

Elke kWh die je laadt via het net kost:
**kost_per_kwh = buy_price / rte + depreciation_eur_kwh**

Dit staat als `breakeven_eur_kwh` bij elk slot in de invoer.

Grid_charge op uur X is winstgevend als en slechts als:
→ er een later uur Y bestaat met `buy_price[Y] > breakeven_eur_kwh[X]`
→ EN op uur X geldt `soc_start_pct < max_soc_pct`

Negatieve prijs = je wordt BETAALD om te laden. Breakeven is dan negatief → altijd grid_charge als SOC < max.

---

## Optimalisatie-algoritme (3 passes)

### Pass 1 — Globale prijsanalyse (doe dit eerst, voor je ook maar één slot invult)
1. Sorteer alle slots op prijs. Markeer: goedkoop (≤ p25), duur (≥ p75), negatief (< 0).
2. Bereken voor elk goedkoop uur: bestaat er een duur toekomstig uur met prijs > breakeven_eur_kwh van dit uur? Zo ja: dit uur is een grid_charge-kandidaat.
3. Bereken voor elk duur uur: zal er voldoende SOC zijn om te ontladen (SOC > reserve + 10%)? Zo ja: dit uur is een discharge-kandidaat.
4. Identificeer "spaar-uren": uren vlak vóór een duur uur (binnen 4u) met lagere prijs → `save` i.p.v. `neutral`.

### Pass 2 — SOC doorrekenen met geplande acties
Simuleer de SOC door alle 48 uren met de geplande acties:
- grid_charge: bat = min(bat + max_charge_kw × rte, bat_max)
- solar_charge: bat = min(bat + (net_wh/1000) × rte, bat_max)
- discharge: bat = max(bat − consumption_wh/1000, bat_min)
- save: bat ongewijzigd
- neutral: net_wh > 0 → bat = min(bat + net_wh/1000 × rte, bat_max); anders bat = max(bat + net_wh/1000, bat_min)

Controleer: is er voldoende SOC op elk discharge-uur? Is er genoeg ruimte op elk grid_charge-uur?

### Pass 3 — Conflicten oplossen
- Grid_charge uur maar SOC al vol → wijzig naar `solar_charge` (als zonne-overschot) of `neutral`
- Discharge uur maar SOC ≤ reserve → wijzig naar `save` (niet neutral — dan daalt SOC verder!)
- Discharge uur maar geen toekomstig goedkoop uur om bij te laden → overweeg `save` om reserve te houden

---

## Beslisregels per actie (na de 3 passes)

**`grid_charge`** — gebruik als:
- buy_price ≤ p25 OF buy_price < 0.02 (near-zero of negatief)
- EN er bestaat een later uur met prijs > breakeven_eur_kwh van dit slot
- EN soc_start_pct < max_soc_pct
- Goedkope uren overdag zijn even goed als 's nachts — geen tijdsvoorkeur

**`discharge`** — gebruik als:
- buy_price ≥ p75 OF slot is één van de 4 duurste uren per dag
- EN soc_start_pct > min_reserve_soc_pct + 10%
- EN er is voldoende recent opgeladen (geen nutteloze ontlading bij bijna-lege batterij)

**`save`** — gebruik als:
- Huidig uur: prijs < gemiddelde van de komende **2 uur** × 0.90 (max 10% goedkoper dan binnenkort)
- EN er is een discharge-uur **binnen de komende 3 uur** (niet 10+ uur later!)
- EN soc_start_pct > min_reserve_soc_pct + 5%
- **LET OP:** save kost geld (net dekt al het verbruik). Gebruik save enkel als het discharge-uur DICHTBIJ is (≤ 3 uur).
- **NOOIT save als huidig uur zelf ≥ p75** — dan is discharge de correcte actie, ook als er morgen een nog duurder uur is.
- **NOOIT 7+ uur save** voor één ontlaadmoment — herlaad dan liever 's nachts goedkoop en ontlaad tweemaal.

**`solar_charge`** — gebruik als:
- net_wh > 200 Wh (voldoende zonne-overschot)
- EN soc_start_pct < max_soc_pct
- EN buy_price > p25 (anders is grid_charge beter)
- EN buy_price ≥ 0 (negatieve prijs → altijd grid_charge)

**`neutral`** — gebruik als geen van bovenstaande van toepassing is. Dit is de standaard.

---

## ⚠️ Belangrijk: soc_start_pct is een SCHATTING, geen garantie

De `soc_start_pct` waarden in de invoer zijn berekend op basis van `neutral` voor alle toekomstige uren (neutral-baseline).
- Als jij **`save`** kiest: de werkelijke SOC blijft HOGER dan getoond in soc_start_pct.
- Als jij **`grid_charge`** kiest: de SOC stijgt SNELLER dan de baseline toont.
- Als jij **`discharge`** kiest: de SOC daalt SNELLER dan de baseline toont.

**Gebruik je eigen Pass-2 SOC-simulatie** (zie Optimalisatie-algoritme) — vertrouw niet blind op de soc_start_pct invoerwaarden voor toekomstige slots als je een andere actie kiest dan neutral.

---

## Save 's nachts: wanneer wél, wanneer niet

`save` 's nachts (net_wh < 0, geen zon) bevriest de SOC. Het net dekt al het nachtverbruik.

**Save 's nachts is zinvol ALLEEN als:**
- Er later (binnen 12u) een discharge-uur is met prijs > breakeven_eur_kwh
- ÉN de verwachte zonne-opbrengst de volgende dag de batterij NIET volledig kan herladen vanuit een lager startniveau
  (als zon de batterij toch van 15% naar 95% laadt, dan heeft save van 72% → 72% houden nul meerwaarde voor discharge)

**Save 's nachts is NIET zinvol als:**
- Morgen ruim voldoende zon is om de batterij volledig op te laden ongeacht het startniveau → gebruik dan `neutral`
- De nachtprijs (13ct) dicht bij breakeven ligt → het net betalen voor nachtverbruik kost evenveel als 's avonds ontladen
- Er geen duidelijk discharge-uur is later

**Richtlijn**: Bij een verwachte zonneopbrengst > 5 kWh de volgende dag → `neutral` 's nachts (batterij draint, zon laadt terug). Bij bewolkte dag met weinig zon én hoge avondprijzen → `save` tot ca. 06:00 overwegen.

---

## Discharge: gebruik de volle capaciteit

**Harde regel:** Als `buy_price ≥ p75` EN `soc_start_pct > min_reserve_soc_pct + 10%` → **ALTIJD discharge**.
De enige uitzondering: er is een uur binnen de komende **2 uur** met prijs > huidige prijs × 1.10 (meer dan 10% duurder). Dan mag je dat ene uur afwachten.

- Alle uren met prijs ≥ p75 EN SOC hoog genoeg = discharge. Geen uitzonderingen voor "spaar voor morgen".
- Kies **nooit** save of neutral tijdens p75+-uren als de SOC hoog is — dat is aantoonbaar gemiste winst.
- De discharge_kwh is beperkt tot het huisverbruik in dat uur. Meerdere discharge-uren = meer totale besparing.
- Een SOC van 95% met slechts 2 kleine ontlaaduren is verspilling. Plan discharge op ALLE p75+-uren.

---

## Dubbele winst: ontladen nu + herladen goedkoop + ontladen morgen

Dit is de meest winstgevende strategie wanneer alle drie van toepassing zijn:
1. Huidig uur: prijs ≥ p75 (ontladen loont nu)
2. Tussenin (komende nachtur): prijs < breakeven (goedkoop herladen mogelijk)
3. Morgen: duur piekuur > breakeven van nachtlading (ontladen loont dan ook)

**Voorbeeld:** SOC 63%, uur 20:00 (€0.158 = p75), nacht 01:00 (€0.123 < breakeven), morgen 07:00 (€0.200)
- ❌ Fout: save tot 07:00 → eenmalig ontladen = €0.158 × X kWh bespaard
- ✅ Correct: discharge 20:00 (€0.158) + grid_charge 01:00 (€0.123) + discharge 07:00 (€0.200)
  = dubbele ontlading, netto extra winst per kWh = (0.200 − 0.123/RTE−depreciation) + 0.158

**Conclusie:** Als p75+-uur aanwezig is én goedkope nachtlading tussenin mogelijk is → ontlaad NU ook, herlaad 's nachts, ontlaad morgen. Wacht NIET 7+ uur met een volle batterij op één goed moment.

---

## Sluipverbruik 's nachts

`battery.standby_w` in de invoer is het gemeten gemiddeld nachtverbruik (02:00–06:00), automatisch berekend uit historische data. Dit is het meest betrouwbare cijfer voor basisverbruik.

- Bij `neutral` 's nachts daalt de SOC elk uur met: `standby_w / (capacity_kwh × 1000) × 100%`
- Voorbeeld: standby_w = 300 W, cap = 10 kWh → −3% per uur → −18% over 6 uur
- Gebruik dit om te berekenen hoe laag de SOC zal zijn bij zonsopgang als je `neutral` kiest
- Als standby_w = 0: gebruik consumption_wh van de nachtslots als benadering

**Toepassing:** Als de verwachte SOC bij zonsopgang (06:00) na een `neutral`-nacht onder `min_reserve_soc_pct + 10%` zou komen, plan dan `grid_charge` in één of meer nachturen om voldoende SOC te garanderen voor de ochtendpiek.

---

## Nachtlading voor ochtendpiek: prijsverschil berekening

Doe deze analyse **altijd** wanneer er een ochtendpiek is (uren 06:00–10:00 met prijs ≥ p75):

### Stap 1 — Bereken verwachte SOC bij zonsopgang
```
uren_tot_06u = aantal uren van nu tot 06:00
drain_pct = (standby_w / (capacity_kwh × 1000)) × 100 × uren_tot_06u
soc_bij_06u = huidig_soc - drain_pct   (bij neutral de hele nacht)
```

### Stap 2 — Bepaal benodigde SOC voor ochtendpiek
```
piek_verbruik_kwh = som(consumption_wh voor uren met prijs ≥ p75, 06:00–10:00) / 1000
soc_nodig = (piek_verbruik_kwh / capacity_kwh) × 100 + min_reserve_soc_pct
```

### Stap 3 — Is nachtlading winstgevend?
```
tekort_pct = max(0, soc_nodig - soc_bij_06u)
```
Als `tekort_pct > 0`:
- Goedkoopste nachtuur (00:00–05:00): `nacht_prijs`
- Ochtendpiekprijs: `ochtend_prijs`
- Break-even nachtlading: `nacht_prijs / rte + depreciation`
- **Laad als:** `ochtend_prijs > nacht_prijs / rte + depreciation`
  → kies `grid_charge` in het goedkoopste nachtuur (of meerdere uren als tekort groot is)

### Stap 4 — Hoeveel uren laden?
```
kwh_per_laaduur = max_charge_kw × rte
uren_nodig = ceil(tekort_pct × capacity_kwh / 100 / kwh_per_laaduur)
```
Plan grid_charge in de `uren_nodig` goedkoopste nachturen (00:00–05:00).

**Voorbeeld:**
- SOC nu 45%, standby 300W, cap 10kWh, 8u tot 06:00 → drain = 2.4% per uur × 8 = 24% → SOC bij 06:00 = 21%
- Ochtendpiek 07:00-08:00: 2 × 300Wh = 0.6 kWh nodig → soc_nodig = 6% + 15% reserve = 21%
- Tekort = 0% → geen lading nodig ✓
- **Maar:** als drain 30% is → SOC bij 06:00 = 15% = precies reserve → tekort = 6% → 1 uur grid_charge 's nachts nodig
- Laad dan op het goedkoopste nachtuur (bijv. 02:00 of 03:00) als ochtendprijs > breakeven

---

## Verboden combinaties (harde constraints)
- ❌ `discharge` als soc_start_pct ≤ min_reserve_soc_pct
- ❌ `grid_charge` als soc_start_pct ≥ max_soc_pct
- ❌ `solar_charge` als net_wh ≤ 0
- ❌ `solar_charge` bij negatieve of near-zero prijs (< 0.02 €/kWh) → `grid_charge`
- ❌ `neutral` als doel is lading bewaren → gebruik `save`
- ❌ `save` als soc_start_pct < min_reserve_soc_pct + 5% (geen lading te bewaren)
- ❌ `save` als huidig uur buy_price ≥ p75 → dit is een discharge-uur, niet een spaarmomenten
- ❌ `save` langer dan 3 opeenvolgende uren wanneer er tussenin goedkope nachtlading mogelijk is
- ❌ `grid_charge` als geen enkel toekomstig uur prijs > breakeven_eur_kwh heeft (verlieslatend)

---

## Historische context (indien aanwezig in invoer)

Als de invoer een `historical_context` blok bevat, gebruik dit dan actief.

### historical_context.price — historisch prijsprofiel
- **Afwijkingsdetectie**: Als de huidige prijs voor weekdag X, uur Y meer dan 20% onder het historisch gemiddelde ligt → extra kans voor `grid_charge`.
- **Weekendpatroon**: Prijzen in het weekend zijn typisch anders dan door de week → detecteer dit in het profiel.
- Vertrouw altijd meer op de actuele prijzen in de slots dan op het historisch profiel.

### historical_context.plan_accuracy — plan vs werkelijkheid
Als aanwezig, bevat dit een vergelijking van vorige plannen met werkelijke InfluxDB-metingen:
- `solar_forecast.avg_bias_pct`: positief = prognose was te optimistisch (meer zon voorspeld dan werkelijk).
  → Bij positieve bias > 10%: voeg backup grid_charge toe bij bewolkt verwacht; vertrouw solar_charge minder.
- `consumption_forecast.avg_bias_pct`: negatief = verbruik was onderschat.
  → Bij negatieve bias > 10%: reserveer meer batterijcapaciteit, minder agressief discharge.
- `soc_plan_mae_pct`: gemiddelde absolute afwijking tussen geplande en werkelijke SoC.
  → Bij > 5%: houd ruimere veiligheidsmarge aan, niet te dicht op reserve-grens plannen.
- `advice`: lijst van concrete aanbevelingen op basis van gedetecteerde bias — volg deze actief op.

### historical_context.soc — historisch SoC-profiel (werkelijke batterijlading)
Dit zijn de WERKELIJK GEMETEN SoC-waarden uit de database — niet gesimuleerd.

- **Anticipeer op lage SoC-momenten**: Als het profiel toont dat de SoC op maandagochtend 07:00 typisch 25% is, plan dan grid_charge de nacht ervoor zodat de batterij voldoende geladen is.
- **Vermijd overbodige grid_charge**: Als het profiel toont dat de SoC op zondag 14:00 typisch 90% is (veel zon), is grid_charge zaterdagnacht niet nodig.
- **Patroonherkenning**: Lage p25 = het lukt soms niet om de batterij op te laden; hoge p75 = er is typisch genoeg zon/lading. Gebruik dit om de "veiligheidsmarges" in je plan aan te passen.
- **Combineer met prijsprofiel**: Goedkope uren + historisch lage SoC = grid_charge prioriteit.

Vermeld in je `reason` als je historische data gebruikt: bijv. "hist. SoC ma 07u gem. 25% → grid_charge nacht" of "hist. avg €0.12 vs huidig €0.06 → goedkoop".

---

## Antwoord
Gebruik de `submit_battery_plan` tool. Verplichte velden per slot:
- **time**: exact kopiëren van het invoerveld "time"
- **action**: één van: solar_charge / grid_charge / save / discharge / neutral
- **reason**: max 80 tekens, leg concreet uit (prijs, SOC, vergelijking met breakeven)

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

    for slot_dt in all_slots:
        key   = slot_dt.isoformat()
        raw   = price_slots.get(key)
        buy   = (raw + markup) if raw is not None else None
        solar = round(solar_by_slot.get(key, 0.0))
        cons  = round(_cons(slot_dt.weekday(), slot_dt.hour))
        net   = solar - cons

        # Per-slot break-even: minimum future discharge price to make this charge profitable
        slot_be = round(buy / rte + depr, 4) if buy is not None else None

        slots_input.append({
            "time":              key,
            "weekday":           WEEKDAY_NL[slot_dt.weekday()],
            "hour":              slot_dt.hour,
            "buy_price_eur_kwh": round(buy, 4) if buy is not None else None,
            "breakeven_eur_kwh": slot_be,   # grid_charge hier winstgevend als toekomstig uur > dit
            "solar_wh":          solar,
            "consumption_wh":    cons,
            "net_wh":            net,
            "soc_start_pct":     round((_bat_sim / cap_kwh) * 100, 1),
            "is_past":           slot_dt < real_now,
        })

        # Advance simulated SOC assuming neutral (battery covers net consumption)
        if slot_dt >= real_now:
            if net >= 0:
                # Solar surplus: battery charges (up to max)
                _bat_sim = min(bat_max, _bat_sim + (net / 1000.0) * rte)
            else:
                # Consumption exceeds solar: battery drains (down to reserve)
                _bat_sim = max(bat_min, _bat_sim + net / 1000.0)

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
            system=_SYSTEM_PROMPT,
            tools=[tool_def],
            tool_choice={"type": "any"},
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
    _in_tok  = getattr(response.usage, "input_tokens",  0) or 0
    _out_tok = getattr(response.usage, "output_tokens", 0) or 0
    _eur     = _in_tok * _PRICE_IN_EUR_PER_TOKEN + _out_tok * _PRICE_OUT_EUR_PER_TOKEN
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
        cost_eur=round(_eur, 6),
        stop_reason=getattr(response, "stop_reason", None),
        slots_sent=len(slots_input),
        slots_received=len(plan_actions),
        action_counts=action_counts,
        breakeven_eur_kwh=breakeven,
        price_history_days=_history_days,
        soc_history_days=_soc_history_days,
        post_process_overrides=_override_count,
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
