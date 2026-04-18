# Changelog

## [1.19.76] - 2026-04-18

### Fixed
- **Save bij lege batterij**: `save` wordt automatisch omgezet naar `neutral` wanneer de gesimuleerde
  SOC ≤ `min_reserve + 5%`. Concreet geval: SOC 16% met reserve 15% → slechts 1% boven reserve →
  `save` bevriest de batterij terwijl het net alles dekt, wat extra kosten oplevert zonder enig nut.
  De zon herlaadt de batterij de volgende dag toch ongeacht of SOC 15% of 16% is als startpunt.
- **Systeem-prompt aangescherpt**: twee extra `save`-verboden toegevoegd:
  - `save` meer dan 6 achtereenvolgende nachtturen bij SOC < 25% én morgen > 8 kWh zonverwachting
  - Concreet voorbeeld in prompt verduidelijkt waarom 1% boven reserve zinloos is om te bewaren

## [1.19.75] - 2026-04-18

### Changed
- **Claude-strategie: upgrade naar Sonnet** — standaard model gewijzigd van `claude-haiku-4-5-20251001`
  naar `claude-sonnet-4-6`. Sonnet heeft significant betere redeneerdiepte voor de 3-pass globale
  optimalisatie over 48 slots. Haiku blijft beschikbaar als handmatige keuze in de strategie-instellingen.
- **Prompt caching** — systeem-prompt (~2.000 tokens) wordt nu gecached via `cache_control: ephemeral`.
  Op de tweede en volgende dagelijkse planningsrun zijn de cached input-tokens 90% goedkoper
  ($0.30/MTok i.p.v. $3.00/MTok voor Sonnet). Cache-statistieken zichtbaar in debug-panel.
- **Deterministische output** — `temperature=0.1` toegevoegd. Dezelfde invoer levert nu consistent
  hetzelfde plan, zonder willekeurige variatie die bij financiële optimalisatie ongewenst is.
- **tool_choice aangescherpt** — `{"type": "any"}` → `{"type": "tool", "name": "submit_battery_plan"}`.
  Voorkomt dat Claude een andere tool zou kiezen en zorg voor directe plan-output.
- **Per-model prijstabel** — token-kostberekening ondersteunt nu Haiku 4.5, Sonnet 4.6 en Opus 4.7
  met correcte cache-write en cache-read tarieven per model.

### Fixed
- **Feasibility-pass na Claude** — na ontvangst van Claude's plan wordt de SOC nu forward-gesimuleerd
  over alle 48 slots. Onmogelijke acties (discharge bij SOC ≤ reserve; grid_charge bij volle batterij)
  worden automatisch gecorrigeerd naar `save` resp. `neutral` met een `[feasibility]`-prefix in de reden.
  Aantal correcties wordt gelogd en zichtbaar in het debug-panel (`feasibility_overrides`).

## [1.19.74] - 2026-04-07

### Fixed
- **SOC-bronprioriteit**: `_live_soc()` probeert nu HA-sensoren als eerste bron (altijd
  gecached in HA, ongeacht InfluxDB), dan `last_soc.json`, dan ESPHome directe poll.
  Hierdoor is de geconfigureerde HA-sensor leidend voor alle SOC-beslissingen.
- **ESPHome Dutch firmware**: `_poll_esphome()` NAME_MAP uitgebreid met `["laadniveau"]`
  zodat Nederlandse ESPHome-entiteiten ("Laadniveau (SOC)") correct gematcht worden als SOC.

## [1.19.73] - 2026-04-07

### Added
- Diagnose-endpoint `GET /api/debug/soc` — toont per bron wat de SOC oplevert:
  last_soc.json (leeftijd), ESPHome SSE-poll per apparaat, flow_cfg bat_soc-slot,
  HA-entiteit status en de gecombineerde `_live_soc()` uitkomst. Handig om te zien
  waarom SOC 50% blijft.

## [1.19.72] - 2026-04-07

### Fixed
- **SOC 50% ook in plan-berekening**: `_do_soc()` gebruikte eerst de (falende) lokale InfluxDB
  en kwam dan pas toe aan de ESPHome/HA live-poll. Nu hergebruikt `_do_soc()` de robuustere
  `_live_soc()` als eerste stap (last_soc.json → flow-poll → ESPHome direct), en valt pas daarna
  terug op externe/lokale InfluxDB. Hierdoor krijgt Claude de correcte startSOC ook als de
  lokale InfluxDB niet bereikbaar is — wat direct impact had op de hele planningsimulatie.

## [1.19.71] - 2026-04-07

### Fixed
- **SOC: 50% — definitieve fix**: `_live_soc()` probeert nu drie bronnen in volgorde:
  1. `last_soc.json` (geschreven door datacollector, < 5 min oud)
  2. Directe live-poll via geconfigureerde flow-bronnen (ESPHome + HA) — werkt ook als lokale InfluxDB niet draait
  3. ESPHome directe poll — werkt zelfs zonder bat_soc in flow-config
  Hierdoor toont de strategie-pagina altijd de werkelijke SOC, ook wanneer de lokale InfluxDB
  niet beschikbaar is (Connection refused op localhost:8086).

## [1.19.70] - 2026-04-07

### Fixed
- **SOC: 50% op strategie-pagina**: de header toonde de SOC die ingebakken was bij het
  genereren van het plan (soms 50% fallback). Nu wordt bij elke API-aanroep de live SOC
  uit `last_soc.json` (< 5 min oud) geïnjecteerd vóór het resultaat teruggegeven wordt.
  Hierdoor toont de header altijd de actuele SOC, ook als het plan uren geleden berekend is.
- Fallback-log: wanneer `_do_soc()` geen enkele bron kan lezen, verschijnt nu een warning
  in de logs zodat je kunt zien waarom de fallback 50% gebruikt werd.

## [1.19.69] - 2026-04-07

### Added
- **Zontekort-failsafe in automatisering**: wanneer de automatisering een `solar_charge` of
  `neutral` slot uitvoert maar de zon beduidend minder levert dan voorspeld (< 25% van de
  prognose), én er binnen 14 uur een `discharge`-slot gepland staat, schakelt de automatisering
  automatisch over naar `save`. Zo wordt de huidige SOC bewaard voor de ontlading in plaats van
  stilletjes weg te lekken in neutral-modus terwijl de batterij leeg is.
- Nieuwe helper `_live_soc()` — leest `last_soc.json` (< 5 min oud) voor gebruik buiten
  `_compute_forward_plan()`.
- Nieuwe helper `_check_solar_deficit_save()` — evalueert zontekort-criteria en geeft een
  leesbare override-reden terug voor de UI.

## [1.19.68] - 2026-04-06

### Fixed
- HomeWizard energy socket: `_hw_fetch()` gooit nu een begrijpelijke foutmelding wanneer
  `resp.json()` faalt (niet-HTML maar ook geen geldig JSON response). Vorige gedrag toonde
  cryptische Python JSONDecodeError. Nu: duidelijke melding met Content-Type en body-snippet.
- UI-hint voor "Lokale API" uitgebreid met aparte instructies voor P1-meter en energy socket
  (energy socket zit op ander pad in de HomeWizard app dan P1-meter).

### Added
- Diagnose-endpoint `GET /api/homewizard/probe?ip=X.X.X.X` — toont raw response van alle
  HomeWizard API-paden (/api, /api/v1/data, /api/v1/state) zodat je kunt zien of Lokale API
  ingeschakeld is zonder de add-flow te doorlopen.

## [1.19.67] - 2026-04-06

### Improved
- Claude AI strategie: expliciete 4-staps nachtlading berekening toegevoegd aan de prompt.
  Claude berekent nu zelf of `grid_charge` 's nachts winstgevend is op basis van:
  1. Verwachte SOC bij zonsopgang (standby_w × uren drain)
  2. Benodigde SOC voor ochtendpiek (som verbruik p75+-uren / capaciteit + reserve)
  3. Winstberekening: ochtendprijs > nachtprijs/RTE + afschrijving
  4. Aantal benodigde laaduren (tekort / (max_charge_kw × RTE))
  Hierdoor plant Claude automatisch nachtlading wanneer de batterij te leeg is om
  de ochtendpiek te overbruggen én het prijsverschil nacht→ochtend groot genoeg is.

## [1.19.66] - 2026-04-06

### Improved
- Claude AI strategie: gemiddeld sluipverbruik (`standby_w`, automatisch gemeten uit
  02:00–06:00 historiek) toegevoegd aan de payload en uitgelegd in de prompt.
  Claude kan nu exact berekenen hoe snel de SOC 's nachts daalt bij `neutral`
  (formule: `standby_w / capacity_kwh / 10` % per uur), en op basis hiervan
  bepalen of `grid_charge` nodig is om de ochtendpiek te halen.

## [1.19.65] - 2026-04-06

### Fixed
- Claude AI strategie: "te voorzichtig" discharge-gedrag opgelost.
  - **Harde discharge-regel**: prijs ≥ p75 + SOC > reserve+10% → ALTIJD discharge.
    Enige uitzondering: binnen 2 uur is er een uur > huidige prijs × 1.10.
  - **Save lookahead beperkt tot 3 uur**: save mag alleen uitstel geven naar een
    discharge-uur dat maximaal 3 uur later ligt. 7+ uur sparen voor één piekmoment
    is nu expliciet verboden.
  - **Dubbele-winst strategie toegevoegd**: discharge nu (p75+) + grid_charge 's nachts
    (goedkoop) + discharge morgen (piek) = meer winst dan eenmalig wachten.
    Voorbeeld: 20:00 (€0.158) ontladen + 01:00 (€0.123) herladen + 07:00 (€0.200)
    ontladen is altijd beter dan 7 uur sparen voor alleen 07:00.
  - **Verboden combinaties uitgebreid**: save bij prijs ≥ p75 is nu expliciet verboden;
    save langer dan 3 uur aaneengesloten met goedkope nachtlading tussenin is verboden.

### Added
- README.md: uitgebreide Nederlandstalige en Engelstalige documentatie toegevoegd.

## [1.19.64] - 2026-04-06

### Added
- Claude AI strategie: plan vs werkelijkheid tracking.
  - Bij elke Claude-run worden toekomstige plan-slots opgeslagen in `_plan_history.json`
    (max 3 dagen, daarna gepruned).
  - Bij de volgende run worden voltooide slots vergeleken met InfluxDB-actuals:
    zonprognose-bias (%), verbruiksbias (%), SoC-afwijking — opgeslagen in `_plan_accuracy.json`
    (rolling 30 dagen).
  - `_get_accuracy_summary()` berekent gemiddelde bias en MAE en vertaalt dit naar
    concrete adviezen (bijv. "Zonprognose 15% te optimistisch → plan grid_charge als backup").
  - Claude ontvangt `historical_context.plan_accuracy` met statistieken en adviezen;
    de prompt instrueert Claude om structurele afwijkingen actief te compenseren.

## [1.19.63] - 2026-04-06

### Added
- Claude AI strategie: historisch SoC-profiel toegevoegd aan de context die Claude ontvangt.
  - Nieuwe `query_soc_history()` in `influx_writer.py`: één bulk-query haalt 32 dagen
    werkelijke `bat_soc` op (uurgemiddelden uit InfluxDB) in plaats van 32 losse queries.
  - Cache in `_soc_history.json`, wordt maximaal elke 6 uur ververst.
  - Per (weekdag × uur): avg, P25, P75 van de werkelijke batterijlading opgebouwd.
  - Claude ontvangt `historical_context.soc.weekly_soc_profile` naast het bestaande
    prijsprofiel — kan hiermee anticiperen op typisch lage SoC-momenten (bijv. maandag
    07:00 gemiddeld 25% → grid_charge zondagnacht plannen) zonder hardgecodeerde regels.

## [1.19.62] - 2026-04-06

### Fixed
- Claude AI strategie: post-processing failsafe toegevoegd — als Claude `save` plant op een slot
  met zonne-overschot (net_wh > 200 Wh) én de batterij is nog niet vol (SOC < max_soc − 1%),
  wordt de actie automatisch omgezet naar `solar_charge`. `save` bevriest de hardware volledig
  waardoor zonne-energie anders verloren gaat; deze override zorgt dat zonne-overschot altijd
  wordt opgeslagen tenzij de batterij echt vol is.

## [1.19.61] - 2026-04-06

### Fixed
- Claude AI strategie-prompt: `save` 's nachts veroorzaakte een circulaire redenering waarbij
  Claude de neutral-gebaseerde `soc_start_pct` simulatiewaarden zag dalen en concludeerde dat
  de reserve beschermd moest worden — terwijl `save` de SOC juist bevroren houdt op de
  huidige waarde. Toegevoegd:
  - Expliciete waarschuwing dat `soc_start_pct` een neutral-baseline schatting is, geen
    garantie — Claude moet zijn eigen Pass-2 SOC-simulatie volgen bij afwijkende acties
  - Duidelijke richtlijn: `save` 's nachts alleen zinvol als zon de volgende dag de batterij
    NIET volledig herlaadt vanuit lager startniveau; bij ruim zonne-aanbod altijd `neutral`
  - `discharge` uitgebreid: alle uren met prijs ≥ p75 én voldoende SOC moeten ontladen —
    een volle batterij laten staan op dure uren is expliciet verboden als verspilling

## [1.19.60] - 2026-04-06

### Added
- Claude AI strategie: historische prijspatronen — dagelijks worden alle-in prijzen opgeslagen
  in `_price_history.json` (max 32 dagen). Vanaf 3 dagen data ontvangt Claude een
  `weekly_price_profile` per (weekdag × uur) met gemiddelde, P25 en P75 — zodat het model
  zelf afwijkingen kan detecteren (bijv. "huidig uur 40% goedkoper dan historisch gemiddeld →
  extra grid_charge kans") zonder hardgecodeerde patronen.

### Fixed
- `_hw_devices()` en `_save_hw_devices()` omgeven met try/except: een beschadigd of leeg
  `homewizard_devices.json`-bestand veroorzaakte een onafgevangen JSONDecodeError waardoor
  Flask HTML 500 teruggaf i.p.v. JSON — dit was de oorzaak van "Unexpected token '<'" bij
  het toevoegen van HomeWizard energy sockets.

## [1.19.59] - 2026-04-06

### Improved
- Claude AI strategie-prompt volledig herschreven:
  - Break-even berekening per slot (`buy_price/rte + depreciation`) in de invoer meegegeven
    zodat Claude per uur exact weet wanneer netladen winstgevend is
  - 3-pass globaal optimalisatie-algoritme (prijscurve analyseren → SOC doorrekenen →
    conflicten oplossen) i.p.v. greedy stap-voor-stap per uur
  - `save` vs `neutral` tegenstrijdigheid opgelost: strikt onderscheid en expliciete
    verbodsbepaling "nooit neutral als doel is lading bewaren"
  - Hardgecodeerd dagpatroon (00-06u → neutral) verwijderd — regels prevaleren altijd
  - Grid_charge regels geconsolideerd naar één sectie (was verspreid over 3+ plekken)
  - Lookahead-logica: expliciet framework voor "spaar lading voor duur uur"
  - SOC-cap in simulatieformules toegevoegd

## [1.19.58] - 2026-04-06

### Added
- HomeWizard energy sockets en alle andere HW-apparaten kunnen nu als
  "verbruiker" worden ingesteld via een dropdown (wasmachine, droogkast,
  vaatwasser, oven, warmtepomp, laadpaal, koelkast, TV, computer, verlichting,
  stopcontact) — instelling opgeslagen via nieuw PATCH-endpoint
- Verbruikers verschijnen als kleine diamant-nodes onderaan de live
  vermogensstroomkaart (EnergyMap), verbonden aan het huis, met live wattage
- Desktop/mobiel toggle (📱/🖥️) naast de thema-knop — simuleert mobiele
  weergave op desktop

### Fixed
- `_hw_fetch`: HTML-responses van HomeWizard apparaten (bijv. lokale API niet
  ingeschakeld) geven nu een duidelijke foutmelding i.p.v. een onduidelijke
  JSON-parsefout in de browser
- `hw_add_device`: null-check toegevoegd voor request body

## [1.19.57] - 2026-04-06

### Fixed
- Live vermogensstroom (EnergyMap): achtergrond gebruikt nu `var(--bg-card)` i.p.v.
  hardgecodeerd donker `#080d18` — thema-bewust in licht én donker thema
- Inactieve stroombanen en node-labels gebruiken nu CSS-variabelen
  (`--border`, `--text-muted`, `--text-dim`) i.p.v. hardgecodeerde donkere kleuren
- Donkere vignet-overlay verwijderd (was altijd zichtbaar in licht thema)

## [1.19.56] - 2026-04-06

### Fixed
- Winst dashboard "zonder auto" simulatie: batterij-SoC wordt nu doorgedragen
  tussen opeenvolgende dagen i.p.v. elke dag te resetten naar 50% — vergelijking
  is nu realistischer voor periodes van meerdere dagen

## [1.19.55] - 2026-04-06

### Fixed
- PV-limiter entiteitskiezer toont nu ook `sensor.*` entiteiten naast `number.*`
  en `input_number.*` — zodat bijv. `sensor.sb4_0_1av_40_247_active_power_limitation`
  gewoon geselecteerd kan worden in entiteitsmodus
- Backend: als de geselecteerde entiteit een `sensor.*` is én er een service
  geconfigureerd is (bijv. `pysmaplus.set_value`), schakelt de backend automatisch
  over naar servicemodus — geen manuele toggle meer nodig

## [1.19.54] - 2026-04-06

### Fixed
- Live vermogensstroom display: achtergrond en tekst gebruiken nu CSS-variabelen
  (`--bg-card`, `--border`, `--text-muted`, `--text-dim`) i.p.v. hardgecodeerde
  donkere kleuren — display ziet er nu correct uit in zowel licht als donker thema

## [1.19.53] - 2026-04-06

### Changed
- PV-limiter gebruikt nu live SOC uit `last_soc.json` (max 5 min oud)
  i.p.v. de geschatte planwaarde — correctere beslissing over "vol/niet vol"
- Batterij niet vol → target = huis + max laadvermogen + marge
- Batterij vol (SOC ≥ max_soc−2%) → target = huis + marge
- Vloer altijd ≥ huisverbruik zodat de limiter nooit grid-import veroorzaakt
- PV-limiter tick versneld van 15s naar 5s

## [1.19.52] - 2026-04-06

### Added
- PV-limiter diagnostische logging: debug bij elke tick, warning bij mislukte HA-aanroep

## [1.19.51] - 2026-04-06

### Fixed
- PV-limiter werkte niet direct na herstart: `_price_cache` was leeg,
  waardoor de limiter tot 60s wachtte op de automation-tick voor prijsdata.
  Nu worden bij opstarten direct de Frank/ENTSO-E prijzen opgehaald in een
  achtergrondthread, zodat de limiter al na 15s actief kan zijn.

## [1.19.50] - 2026-04-06

### Fixed
- PV-limiter target berekening houdt rekening met batterijmodus:
  bij **DISCHARGE** levert de batterij al stroom aan het huis → target is
  `huis - batterij + marge` (lager), zodat solar niet onnodig hoog staat
  bij **GRID_CHARGE** moet solar ook het laden dekken → target is hoger
  bij **ANTI_FEED/NEUTRAL** geen aanpassing

## [1.19.49] - 2026-04-06

### Fixed
- PV-limiter werkt nu onafhankelijk van de batterijautomatisering — ook als
  "Automatisering" uitstaat schakelt de PV-limiter in bij negatieve prijzen
- PV-limiter heeft nu een fallback prijslookup via de prijscache: als er
  nog geen strategie-plan is berekend (bijv. na herstart), wordt de huidige
  uurprijs direct uit de Frank/ENTSO-E cache gelezen

## [1.19.48] - 2026-04-06

### Added
- PV-limiter service mode: entity picker toont alle HA-entiteiten zodat
  `sensor.*` entiteiten (zoals `sensor.sb4_0_1av_40_247_active_power_limitation`)
  ook geselecteerd kunnen worden
- "Extra veld in data" configureerbaar: gebruik `entity_id` voor
  `pysmaplus.set_value`, of `parameter` voor SMA Devices Plus

### Fixed
- Bij uitschakelen van de PV-limiter stuurt de app nu automatisch het
  maximaal vermogen (`pv_limiter_max_w`) terug naar de omvormer — geen
  manuele reset meer nodig

## [1.19.47] - 2026-04-06

### Added
- PV-limiter: aanstuurmethode toggle — kies tussen standaard HA-entiteit
  (`number.set_value`) of aangepaste HA-service (bijv. SMA Devices Plus)
- Service-modus: configureer service (`domein.service`) + parameter naam
  (bijv. `Active Power Limitation`) — app stuurt `{parameter, value}` naar de service
- Preview in UI toont de exacte service-aanroep die verstuurd wordt

## [1.19.46] - 2026-04-06

### Added
- PV-limiter instellingen in de ⚡ Bronnen-tab: kies de HA `number.*` entiteit
  van je omvormer (bijv. SMA Sunny Boy maximaal AC-vermogen) uit een
  doorzoekbare dropdownlijst
- Instellingen: aan/uit toggle, maximaal PV-vermogen (W), prijsdrempel (ct/kWh)
  en extra marge (W)
- Backend: `/api/strategy/settings` accepteert nu ook PATCH naast POST

## [1.19.45] - 2026-04-05

### Fixed
- Winst dashboard: net_w mapping respecteert nu het "omk." vinkje uit de
  InfluxDB-instellingen — zet dit aan als positief in jouw InfluxDB teruglevering
  betekent (i.p.v. de standaard positief=import conventie)

## [1.19.44] - 2026-04-05

### Fixed
- Winst dashboard: gebruikt nu de extern geconfigureerde InfluxDB (zelfde als
  de rest van de app) i.p.v. de interne localhost:8086 die niet beschikbaar is
- Winst dashboard: groupBy doorgaf dag-object i.p.v. datum-string aan
  isoWeekLabel/monthLabel → week/maand labels toonden NaN
- Winst dashboard: betere foutmelding bij geen data (vermeldt de drie
  vereiste configuratiestappen)

## [1.19.43] - 2026-04-05

### Added
- Winst dashboard: sub-tab "📅 Dag" toont vandaag per uur als standaard weergave;
  ◀ ▶ navigatie om dag voor/achteruit te bladeren
- Per-uur staafgrafiek (gegroepeerd: rood=zonder auto, groen=met auto) met
  hover-tooltip (prijs, zon, verbruik, net, besparing)
- Dagsamenvattingspills boven de uurbalken (kost zonder/met auto + besparing)
- Backend `/api/profit` geeft nu ook vandaag mee (gedeeltelijke data = OK)

### Fixed
- Winst overzicht toont beschikbare data ook als de gekozen periode niet
  volledig gevuld is (geen foutmelding bij minder data dan gevraagd)

## [1.19.42] - 2026-04-05

### Added
- Winst dashboard: geëxtrapoleerde besparingsrij toont +X€/dag · +Y€/week ·
  +Z€/maand · +W€/jaar op basis van het gemiddelde van de geselecteerde periode
- Winst dashboard: weekoverzicht-tabel (≥ 2 weken data) met besparing per week
- Winst dashboard: maandoverzicht-tabel (≥ 20 dagen data) met besparing per maand
- Beide tabellen tonen: zonder auto / met auto / besparing + visuele balkbreedte

## [1.19.41] - 2026-04-05

### Added
- Nieuw dashboard "Winst" (💰): vergelijkt geschatte energiekosten zonder
  automatisatie (altijd anti-feed, nooit netladen) vs. werkelijke kosten met
  automatisatie (gemeten netafname uit InfluxDB), op basis van historische
  uurprijzen (ENTSO-E of Frank Energie)
- Periodes: 7 / 30 / 90 dagen
- Overzichtskaarten: totale besparing, % bespaard, kosten zonder/met auto
- Dagelijkse staafgrafiek (gegroepeerd) + cumulatieve besparingslijn
- Klikbare dagdetail-tabel per uur (prijs, zon, verbruik, net, beide kosten)
- Backend `/api/profit` endpoint: price fetch (ENTSO-E of Frank historisch)
  + InfluxDB actuals + anti-feed simulatie

## [1.19.40] - 2026-04-05

### Fixed
- Claude AI modus: Frank Energie prijzen kregen onterecht een extra nettarief
  (0.133 €/kWh) bovenop de al-inclusief prijs, waardoor koopprijzen ~13ct te
  hoog lagen en grid_charge minder snel getriggerd werd
- Claude AI prompt: near-negatieve/goedkope middagprijzen triggeren nu
  `grid_charge` ook als er zon is — `solar_charge` werd te snel verkozen boven
  goedkope netstroom; de "1–2 uren per dag" beperking is verwijderd
- Claude AI prompt: `grid_charge` heeft nu expliciet hogere prioriteit dan
  `solar_charge` bij uren met prijs ≤ p25, zodat de batterij zo snel mogelijk
  gevuld wordt met goedkope stroom

## [1.19.39] - 2026-04-05

### Fixed
- "Sparen" modus stuurt nu anti-feed i.p.v. manual+stop: de batterij laadt
  nog steeds van zonneoverschot maar ontlaadt niet geforceerd. Zo wordt
  beschikbaar zonneoverschot tijdens een spaar-uur altijd benut.
- Batterijmodus label "Sparen" gecorrigeerd naar "anti-feed"

## [1.19.38] - 2026-04-05

### Fixed
- Strategie netwerk laden: trigger nu ook als de spread (netto winst per kWh
  na efficiency + slijtage) groter is dan de drempel (standaard 5ct/kWh),
  ook al is de prijs niet absoluut goedkoop (< p25). Nieuwe instelling:
  `min_charge_spread_eur_kwh` (standaard 0.05 = 5ct)
- Werkelijke opbrengst 502: foutbericht bevat nu het echte HA-fout detail

## [1.18.0] - 2026-04-02

### Changed
- Alle uursloten getoond in de detaillijst, inclusief neutrale uren
  (gedimde weergave met 45% opacity)

## [1.17.0] - 2026-04-02

### Fixed
- "Huidig uur: —" definitief opgelost: `_plan_cache["slots"]` gebruikte
  `result.get("slots", [])` maar `split_days()` geeft `"all"` terug, niet
  `"slots"` — waardoor de cache altijd leeg was

## [1.16.0] - 2026-04-02

### Fixed
- `homeassistant_api: true` toegevoegd aan config.yaml — zonder deze vlag
  heeft de add-on geen toegang tot `http://supervisor/core`, waardoor alle
  HA API-aanroepen 401 gaven ondanks het gebruik van de Supervisor-route

## [1.15.0] - 2026-04-02

### Fixed
- HA instellingen opslaan: URL is niet meer verplicht — als add-on werkt
  de Supervisor-verbinding zonder URL

## [1.14.0] - 2026-04-02

### Changed
- HA instellingen: tekst verduidelijkt dat URL en token als add-on niet
  vereist zijn — de Supervisor-verbinding werkt automatisch

## [1.13.0] - 2026-04-02

### Fixed
- HA HTTP 502 / HTML-respons: alle interne HA API-aanroepen (entiteiten,
  history, poll, SOC-opzoek, …) gebruiken nu `http://supervisor/core` +
  `SUPERVISOR_TOKEN` wanneer die beschikbaar is — de enige gegarandeerd
  werkende route vanuit een HA add-on. Eigen URL/token blijft zichtbaar
  in de instellingen maar wordt niet meer gebruikt voor interne calls.

## [1.12.0] - 2026-04-02

### Fixed
- SOC altijd 50%: live data-collector schrijft nu de gemeten SOC weg naar
  `last_soc.json`; strategie leest dit cachebestand als alle andere bronnen
  (InfluxDB extern, lokaal, ESPHome/HA live-poll) niets opleveren
- Werkelijke zonneopbrengst: foutmelding nu zichtbaar onder de grafiek als
  het ophalen mislukt (HA niet bereikbaar, entiteit niet geconfigureerd, …)

## [1.11.0] - 2026-04-02

### Fixed
- Typo "Zoneprognose" → "Zonneprognose" in de strategie statusindicator
- "Huidig uur: —" in automatisatiebalk: automation-component refresht nu
  direct nadat het laadplan geladen is (i.p.v. wachten op 30-sec poll)

## [1.10.0] - 2026-04-02

### Fixed
- Werkelijke zonneopbrengst InfluxDB: tijdstempels werden altijd als UTC
  behandeld; bars verschenen ~2 uur te vroeg. Tijdstempels worden nu expliciet
  van UTC naar lokale tijd omgezet (geen afhankelijkheid van tz()-ondersteuning
  in InfluxDB v1).
- Werkelijke zonneopbrengst HA history: zelfde UTC→lokale-tijd-conversie
  toegepast zodat slots op het juiste uur staan.
- setup_config.py: HA URL werd bij elke herstart overschreven met
  `http://homeassistant:8123` zelfs als de gebruiker een eigen URL had
  ingesteld. Nu wordt de bestaande URL bewaard tenzij het een localhost-variant
  is.

## [1.9.0] - 2026-04-02

### Fixed
- Energieprijzen pagina 404: leading slash verwijderd uit API paden
- Werkelijke zonneopbrengst timezone: UTC query range uitgebreid met ±14u
  zodat Belgische data (UTC+1/+2) niet meer afgekapt wordt

### Added
- Dag navigatie (◀ ▶) op de forecast pagina: blader naar vorige dagen
  voor historische werkelijke opbrengst (cyane balkjes, zonder forecast)

## [1.8.0] - 2026-04-02

### Added
- Solar forecast grafiek: werkelijke opbrengst overlay (cyaan balk over gele
  voorspellingsbalk) voor vergelijking forecast vs realiteit
- Instellingen → Forecast.Solar: "Werkelijke opbrengst bron" selectie —
  kies InfluxDB zonnepanelen slot of een Home Assistant entiteit
- Werkelijk totaal (Wh) + % van voorspelling getoond in de dag-statistieken
- Backend `/api/forecast/actuals` endpoint: haalt 15-min gemiddelden op uit
  InfluxDB of HA history API voor de geselecteerde bron

## [1.7.0] - 2026-04-02

### Fixed
- HA auto-config now always overwrites a wrong stored URL (e.g. `localhost:8123`)
  with `http://homeassistant:8123` whenever `SUPERVISOR_TOKEN` is available,
  unless the user explicitly configured both `ha_url` and `ha_token`

## [1.6.0] - 2026-04-02

### Changed
- Add-on now uses a pre-built Docker image from GHCR instead of building
  locally on the HA device — updates install in seconds instead of minutes
- Added GitHub Actions workflow (`.github/workflows/build.yml`) that
  automatically builds and pushes `amd64` + `aarch64` images on every version tag

## [1.5.0] - 2026-04-02

### Added
- Auto-configure Home Assistant via Supervisor token: when `ha_url` and
  `ha_token` are left blank, the add-on automatically uses `SUPERVISOR_TOKEN`
  + `http://supervisor/core` so all HA entities are immediately accessible
  without any manual setup

## [1.4.0] - 2026-04-02

### Added
- InfluxDB as a live source option in "Vermogensstroom bronnen": configured slots
  (Zonnepanelen, Net, Thuisverbruik, Batterij vermogen, Batterij SOC) now appear
  as a 📊 InfluxDB group alongside ESPHome / HomeWizard / HA entities
- New `/api/influx/live-slots` endpoint: returns the latest value per configured
  InfluxDB slot (bat_soc averaged, others summed)
- `HomeFlow` resolves and polls InfluxDB live values every 10 s when influx
  sources are selected

## [1.3.0] - 2026-04-02

### Fixed
- InfluxDB scan 401: masked password placeholder (`••••••••`) was sent to
  InfluxDB instead of the real stored password — now falls back to the
  stored secret when the UI sends a masked value (same logic as the save endpoint)

## [1.2.0] - 2026-04-01

### Fixed
- InfluxDB v1 Basic Auth encoding error: `'latin-1' codec can't encode characters`
  — credentials are now encoded as UTF-8 + base64 instead of relying on
  `requests`' default latin-1 path

## [1.1.0] - 2026-04-01

### Added
- InfluxDB auto-discovery: `influx_use_ha_addon` option connects to the HA
  InfluxDB add-on via Supervisor service discovery (no URL needed)
- Full Configuration tab: HA URL/token, ENTSO-E key, InfluxDB connection,
  timezone, log level — all settable from the HA add-on UI
- Sensitive fields (token, API keys, passwords) masked with *** in Config tab
- `setup_config.py`: reads `/data/options.json` at startup and writes
  individual settings JSON files so web UI and CLI mode both work

### Fixed
- HA ingress compatibility: Flask now injects `<base href="...">` tag based
  on `X-Ingress-Path` header so the app works via Cloudflare/remote access
- All 49 frontend API calls converted to relative paths (`api/...` instead
  of `/api/...`) so they route correctly through the HA ingress proxy
- `vite.config.js`: `base: "./"` so bundled assets use relative paths
- Base Docker image updated to `ghcr.io/home-assistant/*-base-python:3.13-alpine3.21`
  (previous `3.12` tag does not exist in the HA registry)
- Added missing `pytz` dependency (required by `python-frank-energie`)
- Dropped deprecated architectures armhf/armv7/i386

## [1.0.0] - 2026-04-01

### Added
- Initial release
- ENTSO-E day-ahead electricity price integration with charge strategy planner
- Solar forecast integration (Open-Meteo)
- Home Assistant sensor history support for consumption profile
- InfluxDB v1/v2 consumption history support
- ESPHome battery control (mode select, force charge/discharge)
- Automation toggle: auto-applies strategy actions to battery every minute
- Home Assistant add-on with ingress sidebar panel
- Configuration tab: HA token, ENTSO-E key, InfluxDB connection
- Multi-arch Docker image (aarch64, amd64)
