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
_USAGE_FILE         = os.path.join(_DATA_DIR, "claude_usage.json")
_PRICE_HISTORY_FILE = os.path.join(_DATA_DIR, "_price_history.json")


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
- Huidig uur: prijs < gemiddelde van de komende 4 uur × 0.85
- EN komende 4 uur: er is een uur met prijs ≥ p75 of discharge-kandidaat
- EN soc_start_pct > min_reserve_soc_pct + 5% (heeft zin om te bewaren)
- **LET OP:** save kost geld (je koopt dure stroom van het net voor het verbruik nu). Gebruik save enkel als het voordeel van discharge later opweegt.

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

Bij een discharge-uur met prijs ≥ p75:
- Alle uren met prijs ≥ p75 EN SOC > min_reserve_soc_pct + 10% = discharge
- Kies **niet** save of neutral tijdens dure uren als de SOC hoog is — dat is gemiste winst
- De discharge_kwh is beperkt tot het huisverbruik in dat uur (geen teruglevering). Meerdere discharge-uren = meer totale ontlading.
- Een SOC van 95% en slechts 2 ontlaaduren van elk 300Wh is verspilling. Plan discharge op ALLE dure uren.

---

## Sluipverbruik 's nachts
Consumption_wh is altijd > 0 (koelkast, router, standby). Bij `neutral` 's nachts daalt de SOC elk uur.
Voorbeeld: 350 Wh/u op 10 kWh → −3.5% per uur → −21% over 6 uur.

---

## Verboden combinaties (harde constraints)
- ❌ `discharge` als soc_start_pct ≤ min_reserve_soc_pct
- ❌ `grid_charge` als soc_start_pct ≥ max_soc_pct
- ❌ `solar_charge` als net_wh ≤ 0
- ❌ `solar_charge` bij negatieve of near-zero prijs (< 0.02 €/kWh) → `grid_charge`
- ❌ `neutral` als doel is lading bewaren → gebruik `save`
- ❌ `save` als soc_start_pct < min_reserve_soc_pct + 5% (geen lading te bewaren)
- ❌ `grid_charge` als geen enkel toekomstig uur prijs > breakeven_eur_kwh heeft (verlieslatend)

---

## Historisch prijsprofiel (indien aanwezig in invoer)

Als de invoer een `historical_context.weekly_price_profile` bevat, gebruik dit dan actief:

- **Afwijkingsdetectie**: Als de huidige prijs voor weekdag X, uur Y meer dan 20% onder het historisch gemiddelde ligt → extra kans voor `grid_charge`.
- **Patroonherkenning**: Zie je dat elke maandag-ochtend duur is? Plan `save` of hogere SOC zondag-avond.
- **Seizoenseffecten**: Hogere prijzen in winter (minder zon), lagere in zomer → aanpas agressiviteit van grid_charge.
- **Weekendpatroon**: Prijzen in het weekend (za/zo) zijn typisch lager 's ochtends, pieken 's avonds → detecteer dit in het profiel.

Gebruik het historisch profiel als **contextuele prior**, maar vertrouw altijd meer op de actuele prijzen in de slots.
Vermeld in je `reason` (max 80 tekens) als je een historisch patroon gebruikt: bijv. "hist. avg €0.12 vs huidig €0.06 → goedkoop".

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

    # ── Record price history + build weekly profile ───────────────────────
    _record_price_history(price_slots, markup)
    _price_history = _load_price_history()
    _weekly_profile = _compute_weekly_profile(_price_history)
    _history_days   = len(_price_history)

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
    if _weekly_profile and _history_days >= 3:
        payload["historical_context"] = {
            "days_of_history": _history_days,
            "note": (
                f"Historisch prijsprofiel op basis van {_history_days} dagen data. "
                "Elk item: weekdag, uur, avg/p25/p75 all-in prijs (€/kWh). "
                "Gebruik dit om te detecteren of het huidige uur goedkoop of duur is t.o.v. historisch patroon."
            ),
            "weekly_price_profile": _weekly_profile,
        }

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
