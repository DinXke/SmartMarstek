import { useState, useEffect } from "react";

const DEFAULTS = {
  bat_capacity_kwh:     10.0,
  rte:                  0.85,
  depreciation_eur_kwh: 0.06,
  min_reserve_soc:      10,
  max_soc:              95,
  max_charge_kw:        3.0,
  sell_back:            false,
  grid_markup_eur_kwh:  0.12,
  manual_peak_hours:    "",   // stored as comma-sep string in UI
  history_days:         21,
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
  const [vals,    setVals]    = useState(DEFAULTS);
  const [saving,  setSaving]  = useState(false);
  const [success, setSuccess] = useState(false);
  const [error,   setError]   = useState(null);
  const [influx,  setInflux]  = useState(null);

  useEffect(() => {
    fetch("/api/strategy/settings")
      .then((r) => r.json())
      .then((d) => setVals({
        ...DEFAULTS, ...d,
        manual_peak_hours: (d.manual_peak_hours || []).join(", "),
      }))
      .catch(() => {});

    fetch("/api/influx/status")
      .then((r) => r.json())
      .then(setInflux)
      .catch(() => setInflux({ ok: false, error: "Niet bereikbaar" }));
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
        grid_markup_eur_kwh:  parseFloat(vals.grid_markup_eur_kwh),
        history_days:         parseInt(vals.history_days),
        manual_peak_hours:    vals.manual_peak_hours
          .split(",").map((s) => s.trim()).filter(Boolean).map(Number).filter((n) => !isNaN(n)),
      };
      const r = await fetch("/api/strategy/settings", {
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

      {/* Grid markup */}
      <Row label="Nettarief + belasting (€/kWh)"
        desc="Vaste opslag bovenop de marktprijs: distributie, heffingen, btw (excl.). Typisch 10–15 ct/kWh.">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input className="form-input" type="number" step="0.005" min="0" max="0.50" style={{ width: 80 }}
            value={vals.grid_markup_eur_kwh} onChange={(e) => set("grid_markup_eur_kwh", e.target.value)} />
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            ({Math.round(parseFloat(vals.grid_markup_eur_kwh || 0) * 100)} ct)
          </span>
        </div>
      </Row>

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
      <Row label="Max. laadvermogen netwerk (kW)"
        desc="Maximale laadsnelheid van het net (niet de zon).">
        <input className="form-input" type="number" step="0.5" min="0.5" max="11" style={{ width: 80 }}
          value={vals.max_charge_kw} onChange={(e) => set("max_charge_kw", e.target.value)} />
      </Row>

      {/* Sell back */}
      <Row label="Teruglevering mogelijk"
        desc="Kan de installatie terugleveren aan het net? (Invloed op export-strategie)">
        <button className={`toggle ${vals.sell_back ? "on" : ""}`}
          onClick={() => set("sell_back", !vals.sell_back)} type="button" />
      </Row>

      {/* History */}
      <Row label="Historiedagen voor verbruiksprofiel"
        desc="Aantal dagen om gemiddeld verbruik per uur te berekenen uit InfluxDB.">
        <input className="form-input" type="number" step="7" min="7" max="90" style={{ width: 80 }}
          value={vals.history_days} onChange={(e) => set("history_days", e.target.value)} />
      </Row>

      {/* Manual peak hours */}
      <Row label="Manuele piekuren (overschrijft automatisch)"
        desc="Komma-gescheiden uren (0–23) waarop het verbruik het hoogst is. Leeg = automatisch afleiden uit historiek. Bv: 7, 8, 18, 19, 20">
        <input className="form-input" type="text" style={{ width: 240 }}
          placeholder="bv: 7, 8, 18, 19, 20 (leeg = automatisch)"
          value={vals.manual_peak_hours} onChange={(e) => set("manual_peak_hours", e.target.value)} />
      </Row>

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
