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
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("strategy_claude")

WEEKDAY_NL = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]

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

_SYSTEM_PROMPT = """Je bent een gespecialiseerde AI-agent voor optimaal thuisbatterijbeheer in de Belgische energiemarkt.

## Doel
Stel een uurlijks laadplan op voor de komende 48 uur om de totale elektriciteitskosten te minimaliseren.
Houd rekening met: energieprijzen per uur, zonneopbrengst, verwacht verbruik, batterijstatus en fysieke beperkingen.

## Invoer die je ontvangt
- **battery**: capaciteit (kWh), huidige SOC (%), reservedrempel, maximaal SOC, max laadvermogen (kW), RTE (rendement heen-en-terug, 0–1), afschrijfkost (€/kWh)
- **price_stats**: p25/mediaan/p75/min/max van alle bekende uurprijzen (€/kWh, inclusief nettarief)
- **slots**: uurlijkse tijdslots met: tijdstempel, weekdag, uurprijzen (buy_price_eur_kwh), zonne-opbrengst (solar_wh), verwacht verbruik (consumption_wh), netto (net_wh = solar − verbruik), geschatte SOC aan het begin van het uur

## Beschikbare acties — LEES DIT ZORGVULDIG

### ⚠️ KRITISCH ONDERSCHEID: neutral ≠ save
`neutral` en `save` klinken gelijkaardig maar zijn FUNDAMENTEEL ANDERS:

- **`neutral`** = firmware anti-feed modus. De batterij **ontlaadt actief** om het huisverbruik te dekken.
  Als consumption_wh > 0, zal de batterij altijd stroom leveren bij neutral. De SOC DAALT.
  Gebruik neutral ALLEEN als ontladen op dit moment geen probleem is.

- **`save`** = batterij volledig passief (hardware op "stop"). De batterij doet NIETS.
  Geen laden, geen ontladen. SOC blijft stabiel. Netafname dekt het verbruik volledig.
  Gebruik save als je de lading wil BEWAREN voor een duurder uur dat snel volgt.

### Alle acties:
| Actie | Wat de batterij doet | Typische situatie |
|---|---|---|
| `solar_charge` | Laadt op met zonne-overschot | net_wh > 200 Wh én SOC < max |
| `grid_charge` | Laadt op via het net (actief) | prijs ≤ p25 én een uur > break-even volgt later |
| `save` | **Doet NIETS — SOC gefixeerd** | prijs nu ≤ mediaan maar duurder uur nadert (> 20% meer) binnen 6u |
| `discharge` | Levert consumption_wh aan huis | prijs > p75 of duurste uur, SOC > reserve |
| `neutral` | **Ontlaadt voor verbruik** (anti-feed) | geen bijzondere reden, laat firmware beheren |

## Aanpak — volg deze stappen in volgorde
1. **Prijscurve analyseren**: identificeer goedkope dalen (≤ p25) en dure pieken (≥ p75).
2. **Grid_charge plannen**: laad goedkoop op als break-even < een duurder uur later:
   - Break-even = buy_price / rte + depreciation_eur_kwh (staat in battery.breakeven_grid_charge_eur_kwh)
   - Plan grid_charge alleen bij prijs ≤ p25 én er een uur volgt ≥ break-even.
   - Laad 1–2 goedkoopste uren per dag, niet elk goedkoop uur.
3. **Discharge plannen**: gebruik batterij bij prijs ≥ p75 of de 3–5 duurste uren van de dag, als SOC > reserve.
4. **Save plannen** (PRIORITEIT boven neutral bij hoge prijs nadert):
   - Als huidig uur lagere prijs heeft dan een uur binnen de volgende 6 uur (≥ 20% duurder), gebruik `save`.
   - Voorbeeld: 18u = 0.20€, 19u = 0.23€ → save op 18u, discharge op 19u.
   - "Ik wil de batterij bewaren" = `save`, NIET `neutral`.
5. **Solar_charge**: net_wh > 200 Wh én SOC < max_soc.
6. **Neutral**: enkel als geen van bovenstaande van toepassing is EN huidig uur is geen probleem om te ontladen.

## SOC-simulatie (houd dit bij)
- grid_charge: +max_charge_kw × rte kWh (max tot max_soc_pct)
- solar_charge: +net_wh/1000 × rte (max tot max_soc_pct)
- discharge: −min(consumption_wh/1000, soc_kwh − reserve_kwh)
- **save: ±0 kWh** (batterij bevroren)
- **neutral: −consumption_wh/1000 kWh** (batterij ontlaadt voor verbruik!)
- NOOIT onder min_reserve_soc_pct
- NOOIT boven max_soc_pct

## Sluipverbruik en nachtelijk SOC-verlies
- De woning heeft altijd een basisverbruik (koelkast, router, standby-apparaten): het **sluipverbruik**.
- Dit is zichtbaar in consumption_wh voor uren 00–06: typisch 150–500 Wh/u zelfs als iedereen slaapt.
- Bij **neutral** 's nachts: de batterij ontlaadt voor dit sluipverbruik. De SOC daalt elk uur met ~consumption_wh / capacity_kwh × 100%.
- Voorbeeld: 300 Wh sluipverbruik op 10 kWh batterij = 3% SOC per nachtuur → 6 uur nacht = −18% SOC.
- Het soc_start_pct in de invoer is al gesimuleerd met dit nachtelijk verbruik, zodat je de werkelijke SOC-evolutie ziet.
- Houd hiermee rekening bij grid_charge plannen: als je wil dat de batterij 's ochtends vol genoeg is voor de ochtendpiek, laad dan voldoende op voor de nacht.

## Typisch dagpatroon (Belgische gezinswoning)
- **00–06u**: sluipverbruik (~200–400 Wh/u), prijzen laag → neutral (batterij ontlaadt langzaam, OK)
- **07–09u**: ochtendpiek (500–800 Wh/u), prijs vaak hoog → discharge of save als 2u later nog duurder
- **10–15u**: zonne-overschot → solar_charge; geen zon: neutral
- **16–20u**: avondpiek + hoge prijzen → discharge bij p75-uren; save 1u vóór het duurste uur
- **21–24u**: laag verbruik → neutral of grid_charge als morgen duurder

## Kritische fouten (absoluut vermijden)
- ❌ `neutral` zeggen maar bedoelen "ik wil de lading bewaren" → gebruik `save`
- ❌ `neutral` met reden "spaar voor later" → dat is een contradictie, gebruik `save`
- ❌ discharge als SOC ≤ min_reserve_soc_pct
- ❌ grid_charge als SOC ≥ max_soc_pct
- ❌ solar_charge als net_wh ≤ 0
- ❌ save als SOC al bijna op reserve zit (< min_reserve + 5%)

## Antwoord
Gebruik de submit_battery_plan tool. Verplichte velden per slot:
- **time**: exact kopiëren van het invoerveld "time" (inclusief tijdzone-offset)
- **action**: één van de vijf geldige waarden
- **reason**: korte Nederlandse motivatie (max 80 tekens), leg uit WAAROM deze actie hier logisch is

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
    markup        = float(s.get("grid_markup_eur_kwh", 0.12))
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

        slots_input.append({
            "time":              key,
            "weekday":           WEEKDAY_NL[slot_dt.weekday()],
            "hour":              slot_dt.hour,
            "buy_price_eur_kwh": round(buy, 4) if buy is not None else None,
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
            "breakeven_grid_charge_eur_kwh": breakeven,
        },
        "price_stats": {
            "p25_eur_kwh":    round(p25,    4),
            "median_eur_kwh": round(median, 4),
            "p75_eur_kwh":    round(p75,    4),
            "min_eur_kwh":    round(p_min,  4),
            "max_eur_kwh":    round(p_max,  4),
            "note": (
                f"Prijzen zijn all-in (marktprijs + {markup*100:.0f}ct nettarief). "
                f"Break-even grid_charge = {breakeven*100:.1f}ct/kWh "
                f"(goedkoopste prijs {p_min*100:.1f}ct / RTE {rte} + afschrijving {depr*100:.0f}ct)."
                if breakeven else "Geen prijzen beschikbaar."
            ),
        },
        "slots": slots_input,
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
    _set_debug(
        fallback=False,
        model=model,
        ran_at=datetime.now(timezone.utc).isoformat(),
        elapsed_s=elapsed,
        input_tokens=getattr(response.usage, "input_tokens", None),
        output_tokens=getattr(response.usage, "output_tokens", None),
        stop_reason=getattr(response, "stop_reason", None),
        slots_sent=len(slots_input),
        slots_received=len(plan_actions),
        action_counts=action_counts,
        breakeven_eur_kwh=breakeven,
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
