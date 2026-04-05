import { useState, useEffect } from "react";

const DEFAULTS = {
  bat_capacity_kwh:     10.0,
  rte:                  0.85,
  depreciation_eur_kwh: 0.06,
  min_reserve_soc:      10,
  max_soc:              95,
  max_charge_kw:        3.0,
  sell_back:            false,
  grid_markup_eur_kwh:  0.133,
  // Nettarief componenten (ct/kWh excl. BTW) — referentie: Frank Energie factuur Limburg feb 2026
  grid_afname_ct:           5.75,
  grid_accijns_ct:          5.03,
  grid_gsc_ct:              1.17,
  grid_wkk_ct:              0.37,
  grid_energiebijdrage_ct:  0.20,
  grid_btw_pct:             6,
  manual_peak_hours:    "",
  history_days:         21,
  price_source:         "entsoe",
  consumption_source:   "auto",
  standby_w:            0,
  save_price_factor:    0.30,
  strategy_mode:        "rule_based",
  claude_api_key:       "",
  claude_model:         "claude-haiku-4-5-20251001",
};

function Row({ label, desc, children }) {
  return (
    <div className="settings-row">
      <div>
        <div className="settings-row-label">{label}</div>
        {desc && <div className="settings-row-desc">{desc}</div>}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}

export default function StrategySettings() {
  const [vals,      setVals]      = useState(DEFAULTS);
  const [saving,    setSaving]    = useState(false);
  const [success,   setSuccess]   = useState(false);
  const [error,     setError]     = useState(null);
  const [influx,    setInflux]    = useState(null);
  const [frankStat, setFrankStat] = useState(null);

  useEffect(() => {
    fetch("api/strategy/settings")
      .then((r) => r.json())
      .then((d) => setVals({
        ...DEFAULTS, ...d,
        manual_peak_hours: (d.manual_peak_hours || []).join(", "),
      }))
      .catch(() => {});

    fetch("api/influx/status")
      .then((r) => r.json())
      .then(setInflux)
      .catch(() => setInflux({ ok: false, error: "Niet bereikbaar" }));

    fetch("api/frank/status")
      .then((r) => r.json())
      .then(setFrankStat)
      .catch(() => setFrankStat({ loggedIn: false }));
  }, []);

  const set = (k, v) => setVals((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setSaving(true); setError(null); setSuccess(false);
    try {
      const body = {
        ...vals,
        bat_capacity_kwh:     parseFloat(vals.bat_capacity_kwh),
        rte:                  parseFloat(vals.rte),
        depreciation_eur_kwh: parseFloat(vals.depreciation_eur_kwh),
        min_reserve_soc:      parseInt(vals.min_reserve_soc),
        max_soc:              parseInt(vals.max_soc),
        max_charge_kw:        parseFloat(vals.max_charge_kw),
        grid_afname_ct:          parseFloat(vals.grid_afname_ct)          || 0,
        grid_accijns_ct:         parseFloat(vals.grid_accijns_ct)         || 0,
        grid_gsc_ct:             parseFloat(vals.grid_gsc_ct)             || 0,
        grid_wkk_ct:             parseFloat(vals.grid_wkk_ct)             || 0,
        grid_energiebijdrage_ct: parseFloat(vals.grid_energiebijdrage_ct) || 0,
        grid_btw_pct:            parseFloat(vals.grid_btw_pct)            || 6,
        grid_markup_eur_kwh: (
          (parseFloat(vals.grid_afname_ct)          || 0) +
          (parseFloat(vals.grid_accijns_ct)         || 0) +
          (parseFloat(vals.grid_gsc_ct)             || 0) +
          (parseFloat(vals.grid_wkk_ct)             || 0) +
          (parseFloat(vals.grid_energiebijdrage_ct) || 0)
        ) * (1 + (parseFloat(vals.grid_btw_pct) || 6) / 100) / 100,
        history_days:         parseInt(vals.history_days),
        manual_peak_hours:    vals.manual_peak_hours
          .split(",").map((s) => s.trim()).filter(Boolean).map(Number).filter((n) => !isNaN(n)),
        price_source:         vals.price_source,
        consumption_source:   vals.consumption_source,
        standby_w:            parseFloat(vals.standby_w) || 0,
        save_price_factor:    parseFloat(vals.save_price_factor) || 0.30,
        strategy_mode:        vals.strategy_mode || "rule_based",
        claude_api_key:       vals.claude_api_key || "",
        claude_model:         vals.claude_model   || "claude-haiku-4-5-20251001",
      };
      const r = await fetch("api/strategy/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("Opslaan mislukt");
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  return (
    <div className="settings-section">
      <div className="settings-section-title">🧠 Laadstrategie</div>

      {/* InfluxDB status */}
      <div className="settings-row" style={{ background: influx?.ok ? "rgba(74,222,128,0.06)" : "rgba(248,113,113,0.06)", borderRadius: 8, margin: "0 0 4px" }}>
        <div>
          <div className="settings-row-label">InfluxDB status</div>
          <div className="settings-row-desc">
            Tijdreeksen worden opgeslagen op {influx?.url || "http://localhost:8086"} · bucket: {influx?.bucket || "energy"}
          </div>
        </div>
        <div style={{ fontSize: 12, fontWeight: 600, color: influx?.ok ? "var(--green)" : "var(--red)" }}>
          {influx == null ? "…" : influx.ok ? `✅ ${influx.status}` : `❌ ${influx.error}`}
        </div>
      </div>

      {/* Battery capacity */}
      <Row label="Batterijcapaciteit (kWh)"
        desc="Bruikbare capaciteit van alle batterijen samen.">
        <input className="form-input" type="number" step="0.5" style={{ width: 90 }}
          value={vals.bat_capacity_kwh} onChange={(e) => set("bat_capacity_kwh", e.target.value)} />
      </Row>

      {/* RTE */}
      <Row label="Round-trip efficiëntie (RTE)"
        desc="Energie die eruit komt gedeeld door wat erin gaat. 0.85 = 85%. Wordt gebruikt om laadkosten te berekenen.">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input className="form-input" type="number" step="0.01" min="0.5" max="1" style={{ width: 80 }}
            value={vals.rte} onChange={(e) => set("rte", e.target.value)} />
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            ({Math.round(parseFloat(vals.rte || 0) * 100)}%)
          </span>
        </div>
      </Row>

      {/* Depreciation */}
      <Row label="Afschrijfkost (€/kWh)"
        desc="Kost per kWh die door de batterij gaat (levensduur). Typisch 5–8 ct/kWh.">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input className="form-input" type="number" step="0.005" min="0" max="0.20" style={{ width: 80 }}
            value={vals.depreciation_eur_kwh} onChange={(e) => set("depreciation_eur_kwh", e.target.value)} />
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            ({Math.round(parseFloat(vals.depreciation_eur_kwh || 0) * 100)} ct)
          </span>
        </div>
      </Row>

      {/* Price source */}
      <Row label="Prijsbron voor laadstrategie"
        desc={vals.price_source === "frank"
          ? "Frank Energie all-in tarieven (marktprijs + belasting + opslag). Zet nettarief hieronder op enkel distributiekosten (~5–7 ct/kWh)."
          : "ENTSO-E groothandelsprijzen. Voeg nettarief + belasting toe via de instelling hieronder (typisch 10–15 ct/kWh)."}>
        <div style={{ display: "flex", gap: 8 }}>
          {[{ val: "entsoe", label: "ENTSO-E" }, { val: "frank", label: "Frank Energie" }].map(({ val, label }) => {
            const disabled = val === "frank" && frankStat && !frankStat.loggedIn;
            return (
              <button
                key={val}
                type="button"
                disabled={disabled}
                title={disabled ? "Niet ingelogd bij Frank Energie" : undefined}
                onClick={() => !disabled && set("price_source", val)}
                style={{
                  padding: "4px 12px",
                  borderRadius: 6,
                  border: "1px solid",
                  fontSize: 12,
                  cursor: disabled ? "not-allowed" : "pointer",
                  borderColor: vals.price_source === val ? "var(--accent)" : "var(--border)",
                  background: vals.price_source === val ? "var(--accent)" : "transparent",
                  color: disabled ? "var(--text-muted)" : vals.price_source === val ? "#fff" : "var(--text)",
                  opacity: disabled ? 0.5 : 1,
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
        {vals.price_source === "frank" && frankStat?.loggedIn && (
          <div style={{ fontSize: 11, color: "var(--green)", marginTop: 4 }}>
            ✓ Ingelogd als {frankStat.email}
          </div>
        )}
        {vals.price_source === "frank" && frankStat && !frankStat.loggedIn && (
          <div style={{ fontSize: 11, color: "var(--red)", marginTop: 4 }}>
            ✗ Niet ingelogd — ga naar Prijzen → Frank Energie om in te loggen
          </div>
        )}
      </Row>

      {/* Grid markup — per component */}
      {(() => {
        const afname  = parseFloat(vals.grid_afname_ct)          || 0;
        const accijns = parseFloat(vals.grid_accijns_ct)         || 0;
        const gsc     = parseFloat(vals.grid_gsc_ct)             || 0;
        const wkk     = parseFloat(vals.grid_wkk_ct)             || 0;
        const eb      = parseFloat(vals.grid_energiebijdrage_ct) || 0;
        const btw     = parseFloat(vals.grid_btw_pct)            || 6;
        const totalExclBtw = afname + accijns + gsc + wkk + eb;
        const totalInclBtw = totalExclBtw * (1 + btw / 100);
        const compStyle = { display: "flex", alignItems: "center", gap: 6 };
        const inp = (key, step = 0.01) => (
          <div style={compStyle}>
            <input className="form-input" type="number" step={step} min="0" max="30" style={{ width: 72 }}
              value={vals[key]} onChange={(e) => set(key, e.target.value)} />
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>ct</span>
          </div>
        );
        return (
          <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0" }}>
            <div className="settings-row-label" style={{ padding: "10px 0 4px", fontWeight: 600 }}>
              Nettarieven &amp; belastingen
            </div>
            <div className="settings-row-desc" style={{ marginBottom: 8 }}>
              Vaste opslag bovenop de marktprijs, per component (excl. BTW).
              {vals.price_source === "frank"
                ? " Frank-prijs bevat al energiebelasting/opslagen — vul hier enkel de nettarieven en accijnzen in."
                : " Tel alle componenten op voor de volledige opslag bovenop ENTSO-E groothandelsprijs."}
              {" "}Referentiewaarden: Frank Energie factuur Limburg feb 2026 (274 kWh).
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: "2px 0" }}>
              <Row label="Afnametarief / distributie"
                desc="Distributie- en transportkost Fluvius. Limburg feb 2026: 5,75 ct/kWh">
                {inp("grid_afname_ct")}
              </Row>
              <Row label="Bijzondere accijns"
                desc="Federale energieaccijns. Limburg feb 2026: 5,03 ct/kWh">
                {inp("grid_accijns_ct")}
              </Row>
              <Row label="Groenestroomcertificaten (GSC)"
                desc="Bijdrage hernieuwbare energie. Limburg feb 2026: 1,17 ct/kWh">
                {inp("grid_gsc_ct")}
              </Row>
              <Row label="WKK-certificaten"
                desc="Warmtekrachtkoppeling-bijdrage. Limburg feb 2026: 0,37 ct/kWh">
                {inp("grid_wkk_ct")}
              </Row>
              <Row label="Energiebijdrage"
                desc="Federale energiebijdrage. Limburg feb 2026: 0,20 ct/kWh">
                {inp("grid_energiebijdrage_ct")}
              </Row>
              <Row label="BTW (%)"
                desc="Van toepassing op bovenstaande nettarieven (standaard 6%).">
                <div style={compStyle}>
                  <input className="form-input" type="number" step="1" min="0" max="21" style={{ width: 60 }}
                    value={vals.grid_btw_pct} onChange={(e) => set("grid_btw_pct", e.target.value)} />
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>%</span>
                </div>
              </Row>
            </div>
            <div style={{
              display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12,
              padding: "8px 0 4px", borderTop: "1px dashed var(--border)", marginTop: 4,
            }}>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Totaal excl. BTW: <strong>{totalExclBtw.toFixed(2)} ct/kWh</strong>
              </span>
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent)" }}>
                Totaal incl. BTW: {totalInclBtw.toFixed(2)} ct/kWh
              </span>
            </div>
          </div>
        );
      })()}

      {/* SOC limits */}
      <Row label="Min. reserve SOC (%)"
        desc="Altijd minimaal deze lading bewaren (noodreserve).">
        <input className="form-input" type="number" step="5" min="0" max="30" style={{ width: 80 }}
          value={vals.min_reserve_soc} onChange={(e) => set("min_reserve_soc", e.target.value)} />
      </Row>
      <Row label="Max. laaddoel SOC (%)"
        desc="Laad niet verder dan dit percentage (bv. 95% voor langere levensduur).">
        <input className="form-input" type="number" step="5" min="50" max="100" style={{ width: 80 }}
          value={vals.max_soc} onChange={(e) => set("max_soc", e.target.value)} />
      </Row>

      {/* Max charge rate */}
      <Row label="Max. laadvermogen netwerk (kW) — totaal"
        desc={`Totaal laadvermogen van alle batterijen samen via het net. Bij geforceerd laden (grid_charge) wordt dit evenredig verdeeld over alle geconfigureerde apparaten. Voorbeeld: 5 kW met 2 batterijen = 2500 W per batterij (ingesteld via Forcible Charge Power). Gebruik max. wat jouw netaansluiting toelaat.`}>
        <input className="form-input" type="number" step="0.5" min="0.5" max="11" style={{ width: 80 }}
          value={vals.max_charge_kw} onChange={(e) => set("max_charge_kw", e.target.value)} />
      </Row>

      {/* Sell back */}
      <Row label="Teruglevering mogelijk"
        desc="Kan de installatie terugleveren aan het net? (Invloed op export-strategie)">
        <button className={`toggle ${vals.sell_back ? "on" : ""}`}
          onClick={() => set("sell_back", !vals.sell_back)} type="button" />
      </Row>

      {/* Consumption source */}
      <Row label="Verbruiksprofiel bron"
        desc="Welke databron gebruiken voor het historisch verbruiksprofiel (per uur/weekdag).">
        <select className="form-input" style={{ width: 200 }}
          value={vals.consumption_source} onChange={(e) => set("consumption_source", e.target.value)}>
          <option value="auto">Automatisch (aanbevolen)</option>
          <option value="local_influx">Lokale InfluxDB</option>
          <option value="external_influx">Externe InfluxDB</option>
          <option value="ha_history">Home Assistant historiek</option>
        </select>
      </Row>

      {/* History */}
      <Row label="Historiedagen voor verbruiksprofiel"
        desc="Aantal dagen om gemiddeld verbruik per uur + weekdag te berekenen.">
        <input className="form-input" type="number" step="7" min="7" max="90" style={{ width: 80 }}
          value={vals.history_days} onChange={(e) => set("history_days", e.target.value)} />
      </Row>

      {/* Standby */}
      <Row label="Sluipverbruik (W)"
        desc="Basisverbruik van altijd-aan toestellen (koelkast, modem, …). Wordt auto-berekend uit 02:00–06:00 historiek. Vul in om te overschrijven.">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input className="form-input" type="number" step="10" min="0" max="2000" style={{ width: 90 }}
            placeholder="0 = auto"
            value={vals.standby_w || ""} onChange={(e) => set("standby_w", e.target.value)} />
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>W</span>
        </div>
      </Row>

      {/* Save price factor */}
      <Row label="Spaardrempel (factor)"
        desc={`Hoe groot het prijsverschil moet zijn om nu te sparen voor een duurder uur. 0.30 = de beste komende prijs moet ≥30% hoger zijn dan de huidige prijs. Lager = sneller sparen; hoger = enkel sparen bij grote spreiding. Huidig: ${Math.round(parseFloat(vals.save_price_factor || 0) * 100)}%`}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input className="form-input" type="number" step="0.05" min="0.10" max="1.00" style={{ width: 80 }}
            value={vals.save_price_factor} onChange={(e) => set("save_price_factor", e.target.value)} />
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            ({Math.round(parseFloat(vals.save_price_factor || 0) * 100)}%)
          </span>
        </div>
      </Row>

      {/* Manual peak hours */}
      <Row label="Manuele piekuren (overschrijft automatisch)"
        desc="Komma-gescheiden uren (0–23) waarop het verbruik het hoogst is. Leeg = automatisch afleiden uit historiek. Bv: 7, 8, 18, 19, 20">
        <input className="form-input" type="text" style={{ width: 240 }}
          placeholder="bv: 7, 8, 18, 19, 20 (leeg = automatisch)"
          value={vals.manual_peak_hours} onChange={(e) => set("manual_peak_hours", e.target.value)} />
      </Row>

      {/* Strategy engine */}
      <div className="settings-row" style={{ background: "rgba(99,102,241,0.06)", borderRadius: 8, margin: "8px 0 4px", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
          <div>
            <div className="settings-row-label">Planningsengine</div>
            <div className="settings-row-desc">
              Regelgebaseerd = lokaal algoritme, altijd beschikbaar.
              Claude AI = Anthropic API, vereist API-sleutel. Haiku is ~€0,001/berekening.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
            {[{ val: "rule_based", label: "Regelgebaseerd" }, { val: "claude", label: "Claude AI" }].map(({ val, label }) => (
              <button key={val} type="button"
                onClick={() => set("strategy_mode", val)}
                style={{
                  padding: "4px 12px", borderRadius: 6, border: "1px solid", fontSize: 12, cursor: "pointer",
                  borderColor: vals.strategy_mode === val ? "var(--accent)" : "var(--border)",
                  background:  vals.strategy_mode === val ? "var(--accent)" : "transparent",
                  color:       vals.strategy_mode === val ? "#fff" : "var(--text)",
                }}>
                {label}
              </button>
            ))}
          </div>
        </div>

        {vals.strategy_mode === "claude" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, width: "100%", paddingTop: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <div className="settings-row-label" style={{ marginBottom: 4 }}>Anthropic API-sleutel</div>
                <input className="form-input" type="password" style={{ width: "100%" }}
                  placeholder="sk-ant-…"
                  value={vals.claude_api_key}
                  onChange={(e) => set("claude_api_key", e.target.value)} />
              </div>
              <div style={{ flexShrink: 0 }}>
                <div className="settings-row-label" style={{ marginBottom: 4 }}>Model</div>
                <select className="form-input" style={{ width: 220 }}
                  value={vals.claude_model} onChange={(e) => set("claude_model", e.target.value)}>
                  <option value="claude-haiku-4-5-20251001">Haiku 4.5 — snel &amp; goedkoop (~€0,001)</option>
                  <option value="claude-sonnet-4-5">Sonnet 4.5 — beter redeneren (~€0,01)</option>
                  <option value="claude-opus-4-5">Opus 4.5 — meest intelligent (~€0,05)</option>
                </select>
              </div>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Slaat de batterijstatus, prijzen en verbruiksprofiel op als JSON en stuurt die naar Claude.
              Bij fout of geen sleutel valt het systeem automatisch terug op de regelgebaseerde engine.
            </div>
          </div>
        )}
      </div>

      <div style={{ padding: "12px 20px 4px", display: "flex", gap: 10, alignItems: "center" }}>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving ? "Opslaan…" : "Opslaan"}
        </button>
        {success && <span style={{ fontSize: 12, color: "var(--green)" }}>✓ Opgeslagen</span>}
        {error   && <span style={{ fontSize: 12, color: "var(--red)" }}>{error}</span>}
      </div>
    </div>
  );
}
