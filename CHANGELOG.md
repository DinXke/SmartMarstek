# Changelog

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
