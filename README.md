<p align="center">
  <img src="https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5?logo=homeassistant&logoColor=white" />
  <img src="https://img.shields.io/badge/version-1.27.11-blue" />
  <img src="https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=white" />
  <img src="https://img.shields.io/badge/license-MIT-green" />
</p>

<h1 align="center">⚡ SmartMarstek</h1>

<p align="center">
  <strong>Slimme thuisbatterij-optimalisatie voor Marstek / ESPHome batterijen in Home Assistant</strong><br/>
  <em>Smart home battery optimisation add-on for Marstek / ESPHome batteries in Home Assistant</em>
</p>

---

> **Taal / Language:** [🇳🇱 Nederlands](#-nederlands) · [🇬🇧 English](#-english)

---

# 🇳🇱 Nederlands

## Wat is SmartMarstek?

SmartMarstek is een volledig functionele Home Assistant add-on die je Marstek (of andere ESPHome-gebaseerde) thuisbatterij automatisch optimaliseert op basis van dynamische energieprijzen, zonne-energieprognoses en je historisch verbruik. Het systeem plant elk uur de optimale batterijactie voor de komende 48 uur en past dit automatisch toe — zonder dat je zelf iets hoeft te doen.

Naast automatisering biedt SmartMarstek een uitgebreid webdashboard met live energiestromen, prijsgrafieken, winstanalyse en diepgaande configuratie.

---

## Functionaliteiten

### Batterijbeheer & Automatisering
- **48-uur vooruitplannen** — berekent elk uur de optimale actie voor vandaag én morgen
- **Vijf batterijacties:** Zonneladen · Netwerk laden · Sparen · Ontladen · Neutraal
- **Automatische uitvoering** — stuurt elke minuut de correcte modus naar je ESPHome-apparaat
- **Zonne-overschot detectie** — overschrijft "Sparen" automatisch met "Neutraal" als er onverwacht zonne-overschot is
- **Multi-batterij ondersteuning** — beheert meerdere batterijen tegelijk en verdeelt de laadvermogen evenredig
- **PV-vermogensbegrenzer** — beperkt omvormer bij negatieve/lage prijzen om teruglevering te voorkomen; herstelt automatisch bij normale prijzen

### Prijsintegraties
- **Frank Energie** — alle-in consumentenprijzen (marktprijs + toeslag + belastingen) via OAuth
- **ENTSO-E** — groothandelsprijzen per land en uur (gratis API-sleutel vereist)
- Ondersteuning voor **België, Nederland en andere ENTSO-E-landen**
- Automatische break-even berekening per slot: `inkoopprijs / RTE + afschrijving`

### Zonne-energieprognose
- **forecast.solar** integratie — 48-uur prognose op 15-minuut resolutie
- Configureerbaar: breedtegraad/lengtegraad, systeemvermogen, dakhoek en -richting
- Vergelijking prognose vs werkelijke productie via InfluxDB of Home Assistant entiteit

### Energiemeting & Databronnen
- **ESPHome apparaten** — real-time via SSE-stream (batterij SOC, vermogen, spanning L1/L2/L3)
- **HomeWizard energie-apparaten** — P1-meter, energie-sockets, kWh-meters (lokale API)
- **Home Assistant entiteiten** — elke HA-sensor als databron
- **InfluxDB v1 & v2** — tijdreeksdata opslaan én opvragen (verbruikshistoriek, SOC-profiel)
- **Externe InfluxDB** — optioneel voor gevorderde setups

### Twee Strategiemodi

#### 1. Regelgebaseerd algoritme
Deterministisch, transparant en razendsnel:
- Automatische detectie van piekvraaguren op basis van historisch verbruik
- Negatieve prijsverwerking (gratis laden + anti-export)
- Lookahead-vensters: 8 uur voor laadwinstgevendheid, 16 uur voor ontladingstiming
- Configureerbare drempelwaarden: save-factor, minimale spread, maximale laadvermogen

#### 2. Claude AI Strategie *(optioneel)*
Gebruikt het Anthropic Claude Haiku model als intelligente planningsagent:

**Wat Claude ontvangt per planningsrun:**
- Alle-in prijzen voor 48 uur met per-slot break-even berekening
- Zonne-energieprognose per uur
- Weekdag-bewust historisch verbruiksprofiel
- Huidige batterij-SOC en instellingen

**Historische context (na opbouwperiode):**
| Databron | Beschrijving |
|---|---|
| Prijspatronen (32 dagen) | Gemiddelde/P25/P75 prijs per weekdag × uur — detecteert anomalieën |
| SOC-profiel (32 dagen) | Werkelijke batterijlading per weekdag × uur uit InfluxDB |
| Plan vs werkelijkheid (30 dagen) | Bias van zonprognose, verbruik en SOC-voorspelling |

**Zelflerend:** Na ~1-2 weken data stelt het systeem automatisch adviezen op zoals:
> *"Zonprognose gemiddeld 15% te optimistisch → plan extra grid_charge als backup bij bewolkt."*

**Claude prompt kenmerken:**
- 3-pass globaal optimalisatie-algoritme (prijscurve → SOC simulatie → conflicten oplossen)
- Strikt onderscheid Sparen vs Neutraal (geen contradictie mogelijk)
- Post-processing failsafe: `save` op zonne-overschot slot → automatisch `solar_charge`

**Kosten:** Claude Haiku ~€0.002–0.005 per planningsrun (één keer per dag bij prijswijziging)

### Live Dashboard
- **Isometrische energiestroomkaart (EnergyMap)** — animeerde vermogensstromen: Zon → Net → Huis → Batterij → EV → Verbruikers
- **Verbruikers-nodes** — HomeWizard energy sockets als diamant-nodes (wasmachine, droogkast, vaatwasser, laadpaal, ...)
- **Themakeuze:** Donker · Licht · Matrix (neon)
- **Desktop/mobiel-toggle** — simuleer mobiele weergave op desktop

### Strategiepagina
- Horizontale 48-uur tijdlijn met prijsbalken, zonnegrafiek, verbruik, actieband en SOC-lijn
- Kleurcodering per actie (oranje = zonneladen, rood = netwerkladen, blauw = sparen, groen = ontladen)
- Historische dagweergave met overlay van werkelijke metingen
- Uitklapbaar per uur: prijs, actie, reden, SOC start/einde
- **Fout-resilientie:** als de berekening mislukt toont de pagina het laatste gecachte plan met een "verouderd plan"-indicatie. Alleen als er nog nooit een plan is berekend verschijnt een foutmelding.

### Winstanalyse
- Dagelijkse vergelijking: kosten mét vs zonder automatisering
- Staafdiagram over meerdere weken
- Totale besparing in €, percentage, gemiddeld per dag

### HomeWizard Integratie
- **Auto-discovery** — scant lokaal subnet naar HomeWizard apparaten
- **Apparaattypen:** P1-slimme meter, energie-sockets, kWh-meters, watermeter
- **Energiekaart** — koppel energy sockets als verbruikers aan de energiestroomkaart
- **Lokale API** — geen cloud, directe communicatie (vereist "Lokale API" aan in HomeWizard app)

### Telegram-notificaties *(optioneel)*
Stuur batterij-updates rechtstreeks naar je Telegram-account via de CommunicationAgent:

| Gebeurtenis | Beschrijving |
|---|---|
| `plan_ready` | Nieuw 48-uur batterijplan berekend |
| `grid_charge_opportunity` | Negatieve/lage prijs gedetecteerd — voordelig netwerkladen |
| `esphome_failed` | ESPHome-verbinding verbroken |
| `daily_summary` | Dagelijks overzicht: winst, SOC, zonne-opbrengst |

- Elke gebeurtenistype kan individueel aan/uit gezet worden
- `grid_charge_opportunity` heeft eigen drempelwaarden voor prijs (€/kWh) en SOC (%)
- Sommige meldingen ondersteunen **goedkeuring via Telegram** — reageer met "Ja/Nee" in de chat

**Instellen:** Schakel Telegram in via **Instellingen → Notificaties** in de webinterface, voer je Telegram Chat-ID in en koppel de CommunicationAgent (draait op poort 3001).

---

## Installatie

### Vereisten
- Home Assistant OS of Supervised
- Een ESPHome-gebaseerde batterij (Marstek B2500 of compatibel)
- Optioneel: Frank Energie account of ENTSO-E API-sleutel
- Optioneel: InfluxDB add-on of externe InfluxDB instantie

### Stap 1 — Add-on repository toevoegen

Ga naar **Instellingen → Add-ons → Add-on store → ⋮ → Repositories** en voeg toe:

```
https://github.com/DinXke/SmartMarstek
```

### Stap 2 — Installeer SmartMarstek

Zoek "SmartMarstek" in de add-on store en klik **Installeren**.

### Stap 3 — Basisconfiguratie

In de add-on **Configuratie**-tab:

```yaml
ha_url: "http://homeassistant.local:8123"
ha_token: "jouw_long_lived_access_token"
entsoe_api_key: ""           # optioneel, of gebruik Frank Energie
entsoe_country: "BE"         # BE, NL, DE, FR, ...
timezone: "Europe/Brussels"
influx_use_ha_addon: false
influx_url: ""               # bijv. http://192.168.1.x:8086
influx_version: "v2"
influx_username: ""
influx_password: ""
log_level: "info"
```

### Stap 4 — Start de add-on

Klik **Starten**. De webinterface is beschikbaar via **Ingress** (HA zijbalk) of op poort `5000`.

### Stap 5 — Eerste instellingen in de webinterface

1. **Apparaten** → Voeg je ESPHome batterij toe (IP-adres)
2. **Instellingen → Strategie** → Vul batterijcapaciteit, RTE, min/max SOC in
3. **Instellingen → Tarieven** → Koppel Frank Energie of ENTSO-E
4. **Instellingen → Zon** → Configureer forecast.solar (lat/lon, kW-piek, hoek)
5. **Instellingen → InfluxDB** → Koppel je database voor verbruikshistoriek
6. **Automatisering** → Schakel in ✓

---

## Configuratie-opties (add-on)

| Veld | Type | Standaard | Omschrijving |
|---|---|---|---|
| `ha_url` | string | — | Home Assistant URL |
| `ha_token` | password | — | HA Long-Lived Access Token |
| `entsoe_api_key` | password | — | ENTSO-E Transparency Platform API-sleutel |
| `entsoe_country` | string | `BE` | Landcode (BE, NL, DE, FR, ...) |
| `timezone` | string | `Europe/Brussels` | Tijdzone |
| `influx_use_ha_addon` | bool | `false` | Gebruik ingebouwde HA InfluxDB add-on |
| `influx_url` | string | — | Externe InfluxDB URL |
| `influx_version` | list | `v1` | InfluxDB versie (v1 of v2) |
| `influx_username` | string | — | InfluxDB gebruikersnaam |
| `influx_password` | password | — | InfluxDB wachtwoord |
| `log_level` | list | `info` | Logniveau (trace/debug/info/warning/error) |

---

## Strategie-instellingen (webinterface)

| Instelling | Standaard | Omschrijving |
|---|---|---|
| Batterijcapaciteit | 10 kWh | Bruikbare capaciteit |
| RTE (rendement) | 0.92 | Round-trip efficiency |
| Afschrijving | 0.02 €/kWh | Kosten per gecyclede kWh |
| Min. reserve SOC | 15% | Nooit onder dit niveau ontladen |
| Max. laadniveau SOC | 95% | Niet boven dit niveau laden |
| Max. laadvermogen | 3.0 kW | Maximaal netladen vermogen |
| Nettarief opslag | 0.133 €/kWh | Belastingen bovenop marktprijs (ENTSO-E) |
| Verbruiksvenster | 21 dagen | Historische data voor verbruiksprofiel |
| Strategiemodus | Regelgebaseerd | Regelgebaseerd of Claude AI |
| Telegram ingeschakeld | Uit | Meldingen via Telegram versturen |
| Telegram Chat-ID | — | Jouw Telegram chat-ID |
| Telegram: goedkeuringstijd | 30 min | Hoe lang wachten op jouw antwoord |
| Prijs-drempel netladen | 0,10 €/kWh | Meld kans tot netladen onder deze prijs |
| SOC-drempel netladen | 80% | Meld kans tot netladen onder dit laadniveau |

---

## Architectuur

```
┌─────────────────────────────────────────────────────┐
│                  SmartMarstek Add-on                │
│                                                     │
│  ┌──────────────┐    ┌──────────────────────────┐  │
│  │   React UI   │◄──►│     Flask Backend         │  │
│  │  (Vite/SWC)  │    │     (app.py)              │  │
│  └──────────────┘    │                            │  │
│                      │  ┌──────────┐  ┌────────┐ │  │
│                      │  │strategy  │  │influx_ │ │  │
│                      │  │.py       │  │writer  │ │  │
│                      │  ├──────────┤  │.py     │ │  │
│                      │  │strategy_ │  └────────┘ │  │
│                      │  │claude.py │             │  │
│                      │  └──────────┘             │  │
│                      └──────────────────────────┘  │
└─────────────────────────────────────────────────────┘
         │               │              │
    ESPHome         InfluxDB      Home Assistant
    Batterij         (tijdreeks)   (entiteiten/services)
         │               │              │
   HomeWizard      Frank Energie   ENTSO-E / forecast.solar
   (P1/sockets)    (prijzen)       (prijzen / zon)
         │
   Anthropic Claude API (optioneel)
```

---

## Datapersistentie

Alle configuratie en historische data worden opgeslagen in de HA `/data`-map:

| Bestand | Omschrijving |
|---|---|
| `strategy_settings.json` | Batterij- en strategie-instellingen |
| `homewizard_devices.json` | HomeWizard apparatenlijst |
| `influx_connection.json` | InfluxDB verbindingsinstellingen |
| `flow_cfg.json` | Energiebron-koppelingen |
| `claude_usage.json` | Claude API gebruik en kosten (ledger) |
| `_price_history.json` | 32-daagse prijsgeschiedenis |
| `_soc_history.json` | 32-daagse SOC-historiek (cache) |
| `_plan_history.json` | 3-daagse plangeschiedenis |
| `_plan_accuracy.json` | 30-daagse plan-vs-werkelijkheid statistieken |

---

## Schermafbeeldingen

> *Volg de [GitHub repository](https://github.com/DinXke/SmartMarstek) voor screenshots en demo's.*

---

## Nieuwe versie uitbrengen

Bij elke release moeten de volgende 5 bestanden het nieuwe versienummer bevatten:

| Bestand | Veld |
|---------|------|
| `config.yaml` | `version: "X.Y.Z"` |
| `build.yaml` | `io.hass.version: "X.Y.Z"` |
| `README.md` | versie-badge `![version-X.Y.Z-blue]` |
| `frontend/package.json` | `"version": "X.Y.Z"` |
| `frontend/package-lock.json` | `"version": "X.Y.Z"` — bijgewerkt via `npm install` |

**Stappen:**

1. Pas `config.yaml`, `build.yaml` en `frontend/package.json` aan naar de nieuwe versie.
2. Voer `npm install` uit in `frontend/` zodat `package-lock.json` synchroon loopt.
3. Pas de versie-badge in `README.md` aan.
4. Commit alle 5 bestanden samen.
5. Voeg een entry toe aan `CHANGELOG.md`.
6. Tag de release: `git tag vX.Y.Z && git push origin vX.Y.Z`.

> Home Assistant detecteert de nieuwe versie via `config.yaml`. Als badge, `package.json` of `build.yaml` achterblijft, toont HA de add-on als "al up-to-date" terwijl dat niet klopt.

## Bijdragen

Pull requests en issues zijn welkom. Gebruik de [Issues-pagina](https://github.com/DinXke/SmartMarstek/issues) voor bugrapporten en functieverzoeken.

---

---

# 🇬🇧 English

## What is SmartMarstek?

SmartMarstek is a fully-featured Home Assistant add-on that automatically optimises your Marstek (or other ESPHome-based) home battery using dynamic electricity prices, solar forecasts, and your historical consumption. The system plans the optimal battery action for every hour over the next 48 hours and executes it automatically — no manual intervention needed.

Beyond automation, SmartMarstek provides a rich web dashboard with live energy flows, price charts, savings analysis, and deep configuration.

---

## Features

### Battery Management & Automation
- **48-hour forward planning** — calculates the optimal action for each hour of today and tomorrow
- **Five battery actions:** Solar Charge · Grid Charge · Save · Discharge · Neutral
- **Automatic execution** — sends the correct mode to your ESPHome device every minute
- **Solar surplus detection** — overrides "Save" with "Neutral" when unexpected solar surplus is detected, preventing wasted solar energy
- **Multi-battery support** — manages multiple batteries simultaneously, distributing charge power evenly
- **PV power limiter** — throttles the inverter during negative/low prices to avoid costly export; restores automatically at normal prices

### Price Integrations
- **Frank Energie** — all-in consumer prices (market price + surcharge + taxes) via OAuth
- **ENTSO-E** — wholesale electricity prices by country and hour (free API key required)
- Support for **Belgium, Netherlands, and other ENTSO-E countries**
- Automatic per-slot break-even calculation: `buy_price / RTE + depreciation`

### Solar Forecast
- **forecast.solar** integration — 48-hour forecast at 15-minute resolution
- Configurable: latitude/longitude, system capacity, roof angle and orientation
- Forecast vs actual production comparison via InfluxDB or Home Assistant entity

### Energy Measurement & Data Sources
- **ESPHome devices** — real-time via SSE stream (battery SOC, power, voltage L1/L2/L3)
- **HomeWizard energy devices** — P1 meter, energy sockets, kWh meters (local API)
- **Home Assistant entities** — any HA sensor as a data source
- **InfluxDB v1 & v2** — store and query time-series data (consumption history, SOC profile)
- **External InfluxDB** — optional for advanced setups

### Two Strategy Modes

#### 1. Rule-Based Algorithm
Deterministic, transparent, and extremely fast:
- Automatic peak hour detection from consumption history
- Negative price handling (free charging + anti-export)
- Look-ahead windows: 8 hours for charge profitability, 16 hours for discharge timing
- Configurable thresholds: save factor, minimum spread, maximum charge power

#### 2. Claude AI Strategy *(optional)*
Uses the Anthropic Claude Haiku model as an intelligent planning agent:

**What Claude receives per planning run:**
- All-in prices for 48 hours with per-slot break-even calculation
- Solar forecast per hour
- Weekday-aware historical consumption profile
- Current battery SOC and settings

**Historical context (after build-up period):**
| Data source | Description |
|---|---|
| Price patterns (32 days) | Average/P25/P75 price per weekday × hour — detects anomalies |
| SOC profile (32 days) | Actual battery charge level per weekday × hour from InfluxDB |
| Plan vs actuals (30 days) | Bias of solar forecast, consumption, and SOC predictions |

**Self-learning:** After ~1–2 weeks of data, the system automatically generates advice such as:
> *"Solar forecast averages 15% too optimistic → plan extra grid_charge as backup on cloudy days."*

**Claude prompt features:**
- 3-pass global optimisation algorithm (price curve → SOC simulation → conflict resolution)
- Strict Save vs Neutral distinction (no contradiction possible)
- Post-processing failsafe: `save` on a solar surplus slot → automatically overridden to `solar_charge`

**Cost:** Claude Haiku ~€0.002–0.005 per planning run (once per day on price change)

### Live Dashboard
- **Isometric energy flow map (EnergyMap)** — animated power flows: Solar → Grid → House → Battery → EV → Consumers
- **Consumer nodes** — HomeWizard energy sockets as diamond nodes (washing machine, dryer, dishwasher, EV charger, ...)
- **Theme selector:** Dark · Light · Matrix (neon)
- **Desktop/mobile toggle** — simulate mobile view on desktop

### Strategy Page
- Horizontal 48-hour timeline with price bars, solar chart, consumption, action band, and SOC line
- Colour-coded actions (orange = solar charge, red = grid charge, blue = save, green = discharge)
- Historical day view with overlay of actual measurements
- Expandable per hour: price, action, reason, SOC start/end
- **Fault resilience:** if calculation fails, the page shows the last cached plan with a "stale plan" indicator. A plain error message appears only when no plan has ever been calculated.

### Profit Analysis
- Daily comparison: costs with vs without automation
- Bar chart over multiple weeks
- Total savings in €, percentage, average per day

### HomeWizard Integration
- **Auto-discovery** — scans local subnet for HomeWizard devices
- **Device types:** P1 smart meter, energy sockets, kWh meters, water meters
- **Energy map** — link energy sockets as consumers to the energy flow map
- **Local API** — no cloud, direct communication (requires "Local API" enabled in HomeWizard app)

---

## Installation

### Requirements
- Home Assistant OS or Supervised
- An ESPHome-based battery (Marstek B2500 or compatible)
- Optional: Frank Energie account or ENTSO-E API key
- Optional: InfluxDB add-on or external InfluxDB instance

### Step 1 — Add the repository

Go to **Settings → Add-ons → Add-on store → ⋮ → Repositories** and add:

```
https://github.com/DinXke/SmartMarstek
```

### Step 2 — Install SmartMarstek

Find "SmartMarstek" in the add-on store and click **Install**.

### Step 3 — Basic configuration

In the add-on **Configuration** tab:

```yaml
ha_url: "http://homeassistant.local:8123"
ha_token: "your_long_lived_access_token"
entsoe_api_key: ""           # optional, or use Frank Energie
entsoe_country: "BE"         # BE, NL, DE, FR, ...
timezone: "Europe/Brussels"
influx_use_ha_addon: false
influx_url: ""               # e.g. http://192.168.1.x:8086
influx_version: "v2"
influx_username: ""
influx_password: ""
log_level: "info"
```

### Step 4 — Start the add-on

Click **Start**. The web interface is available via **Ingress** (HA sidebar) or on port `5000`.

### Step 5 — First-time setup in the web interface

1. **Devices** → Add your ESPHome battery (IP address)
2. **Settings → Strategy** → Fill in battery capacity, RTE, min/max SOC
3. **Settings → Tariffs** → Connect Frank Energie or ENTSO-E
4. **Settings → Solar** → Configure forecast.solar (lat/lon, kW-peak, angle)
5. **Settings → InfluxDB** → Connect your database for consumption history
6. **Automation** → Enable ✓

---

## Add-on configuration options

| Field | Type | Default | Description |
|---|---|---|---|
| `ha_url` | string | — | Home Assistant URL |
| `ha_token` | password | — | HA Long-Lived Access Token |
| `entsoe_api_key` | password | — | ENTSO-E Transparency Platform API key |
| `entsoe_country` | string | `BE` | Country code (BE, NL, DE, FR, ...) |
| `timezone` | string | `Europe/Brussels` | Timezone |
| `influx_use_ha_addon` | bool | `false` | Use built-in HA InfluxDB add-on |
| `influx_url` | string | — | External InfluxDB URL |
| `influx_version` | list | `v1` | InfluxDB version (v1 or v2) |
| `influx_username` | string | — | InfluxDB username |
| `influx_password` | password | — | InfluxDB password |
| `log_level` | list | `info` | Log level (trace/debug/info/warning/error) |

---

## Strategy settings (web interface)

| Setting | Default | Description |
|---|---|---|
| Battery capacity | 10 kWh | Usable capacity |
| RTE (efficiency) | 0.92 | Round-trip efficiency |
| Depreciation | 0.02 €/kWh | Cost per cycled kWh |
| Min. reserve SOC | 15% | Never discharge below this level |
| Max. charge SOC | 95% | Never charge above this level |
| Max. charge power | 3.0 kW | Maximum grid charging power |
| Grid markup | 0.133 €/kWh | Taxes on top of market price (ENTSO-E) |
| Consumption window | 21 days | Historical data for consumption profile |
| Strategy mode | Rule-based | Rule-based or Claude AI |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  SmartMarstek Add-on                │
│                                                     │
│  ┌──────────────┐    ┌──────────────────────────┐  │
│  │   React UI   │◄──►│     Flask Backend         │  │
│  │  (Vite/SWC)  │    │     (app.py)              │  │
│  └──────────────┘    │                            │  │
│                      │  ┌──────────┐  ┌────────┐ │  │
│                      │  │strategy  │  │influx_ │ │  │
│                      │  │.py       │  │writer  │ │  │
│                      │  ├──────────┤  │.py     │ │  │
│                      │  │strategy_ │  └────────┘ │  │
│                      │  │claude.py │             │  │
│                      │  └──────────┘             │  │
│                      └──────────────────────────┘  │
└─────────────────────────────────────────────────────┘
         │               │              │
    ESPHome           InfluxDB     Home Assistant
    Battery          (timeseries)  (entities/services)
         │               │              │
   HomeWizard       Frank Energie  ENTSO-E / forecast.solar
   (P1/sockets)     (prices)       (prices / solar)
         │
   Anthropic Claude API (optional)
```

---

## Data persistence

All configuration and historical data are stored in the HA `/data` folder:

| File | Description |
|---|---|
| `strategy_settings.json` | Battery and strategy settings |
| `homewizard_devices.json` | HomeWizard device list |
| `influx_connection.json` | InfluxDB connection settings |
| `flow_cfg.json` | Energy source mappings |
| `claude_usage.json` | Claude API usage and cost ledger |
| `_price_history.json` | 32-day rolling price history |
| `_soc_history.json` | 32-day SOC history cache |
| `_plan_history.json` | 3-day plan history |
| `_plan_accuracy.json` | 30-day plan vs actuals accuracy stats |

---

## Releasing a new version

Each release requires the following 5 files to carry the new version number:

| File | Field |
|------|-------|
| `config.yaml` | `version: "X.Y.Z"` |
| `build.yaml` | `io.hass.version: "X.Y.Z"` |
| `README.md` | version badge `![version-X.Y.Z-blue]` |
| `frontend/package.json` | `"version": "X.Y.Z"` |
| `frontend/package-lock.json` | `"version": "X.Y.Z"` — updated via `npm install` |

**Steps:**

1. Update `config.yaml`, `build.yaml` and `frontend/package.json` to the new version.
2. Run `npm install` inside `frontend/` so `package-lock.json` stays in sync.
3. Update the version badge in `README.md`.
4. Commit all 5 files together.
5. Add an entry to `CHANGELOG.md`.
6. Tag the release: `git tag vX.Y.Z && git push origin vX.Y.Z`.

> Home Assistant detects new versions through `config.yaml`. If the badge, `package.json`, or `build.yaml` lags behind, HA will report the add-on as already up-to-date when it isn't.

## Contributing

Pull requests and issues are welcome. Use the [Issues page](https://github.com/DinXke/SmartMarstek/issues) for bug reports and feature requests.

---

## License

MIT © [DinXke](https://github.com/DinXke)
