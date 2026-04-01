import { useState, useEffect } from "react";

const DEFAULT_STRING = { label: "", kwp: 1.0, az: 0, dec: 35 };

const AZ_PRESETS = [
  { label: "Noord (180°)",   value: 180 },
  { label: "NO (135°)",      value: 135 },
  { label: "Oost (90°)",     value: 90  },
  { label: "ZO (45°)",       value: 45  },
  { label: "Zuid (0°)",      value: 0   },
  { label: "ZW (-45°)",      value: -45 },
  { label: "West (-90°)",    value: -90 },
  { label: "NW (-135°)",     value: -135},
];

export default function ForecastSettings() {
  const [lat,        setLat]        = useState("");
  const [lon,        setLon]        = useState("");
  const [apiKey,     setApiKey]     = useState("");
  const [configured, setConfigured] = useState(false);
  const [hint,       setHint]       = useState("");
  const [strings,    setStrings]    = useState([{ ...DEFAULT_STRING }]);
  const [saving,     setSaving]     = useState(false);
  const [success,    setSuccess]    = useState(false);
  const [error,      setError]      = useState(null);

  useEffect(() => {
    fetch("/api/forecast/settings")
      .then((r) => r.json())
      .then((d) => {
        setConfigured(d.configured);
        setHint(d.apiKeyHint || "");
        if (d.lat) setLat(String(d.lat));
        if (d.lon) setLon(String(d.lon));
        if (d.strings && d.strings.length) setStrings(d.strings);
      })
      .catch(() => {});
  }, []);

  const updateString = (i, key, val) => {
    setStrings((prev) => prev.map((s, idx) => idx === i ? { ...s, [key]: val } : s));
  };

  const addString = () => setStrings((p) => [...p, { ...DEFAULT_STRING }]);
  const removeString = (i) => setStrings((p) => p.filter((_, idx) => idx !== i));

  const save = async () => {
    setSaving(true); setError(null); setSuccess(false);
    try {
      const body = {
        lat: parseFloat(lat),
        lon: parseFloat(lon),
        strings: strings.map((s) => ({
          ...s,
          kwp: parseFloat(s.kwp) || 1,
          az:  parseFloat(s.az)  || 0,
          dec: parseFloat(s.dec) || 35,
        })),
      };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const r = await fetch("/api/forecast/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("Opslaan mislukt");
      if (apiKey.trim()) { setConfigured(true); setHint(`…${apiKey.trim().slice(-4)}`); setApiKey(""); }
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (e) { setError(e.message); }
    finally     { setSaving(false); }
  };

  return (
    <div className="settings-section">
      <div className="settings-section-title">☀️ Forecast.Solar — Zonneopbrengst voorspelling</div>

      {/* API key */}
      <div className="settings-row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
        <div>
          <div className="settings-row-label">API sleutel</div>
          <div className="settings-row-desc">
            Gratis account op{" "}
            <a href="https://forecast.solar" target="_blank" rel="noreferrer"
              style={{ color: "var(--accent)" }}>forecast.solar</a>
            {" "}geeft 15-minuut nauwkeurigheid. Zonder sleutel = 1u resolutie.
          </div>
        </div>
        {configured && (
          <div style={{ fontSize: 12, color: "var(--green)" }}>✅ API sleutel geconfigureerd ({hint})</div>
        )}
        <input className="form-input" type="password"
          placeholder={configured ? "Nieuwe sleutel (optioneel)" : "API sleutel"}
          value={apiKey} onChange={(e) => setApiKey(e.target.value)}
          style={{ maxWidth: 340 }} />
      </div>

      {/* Location */}
      <div className="settings-row">
        <div>
          <div className="settings-row-label">Locatie</div>
          <div className="settings-row-desc">Breedtegraad en lengtegraad van de installatie.</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input className="form-input" placeholder="Lat bijv. 51.05" value={lat}
            onChange={(e) => setLat(e.target.value)} style={{ width: 130 }} />
          <input className="form-input" placeholder="Lon bijv. 3.72" value={lon}
            onChange={(e) => setLon(e.target.value)} style={{ width: 130 }} />
        </div>
      </div>

      {/* PV Strings */}
      <div className="settings-row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 10 }}>
        <div>
          <div className="settings-row-label">PV strings / daken</div>
          <div className="settings-row-desc">
            Voeg per dakrichting een string toe. Waarden worden opgeteld.
            Az: 0 = Zuid, -90 = Oost, 90 = West. Helling: 0 = plat, 90 = verticaal.
          </div>
        </div>
        {strings.map((s, i) => (
          <div key={i} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
            background: "var(--bg-hover)", borderRadius: 8, padding: "10px 12px", width: "100%" }}>
            <input className="form-input" placeholder={`Naam (bijv. Zuid-dak)`}
              value={s.label} onChange={(e) => updateString(i, "label", e.target.value)}
              style={{ flex: "1 1 120px", minWidth: 100 }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <label style={{ fontSize: 10, color: "var(--text-muted)" }}>kWp</label>
              <input className="form-input" type="number" step="0.01" placeholder="kWp"
                value={s.kwp} onChange={(e) => updateString(i, "kwp", e.target.value)}
                style={{ width: 80 }} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Richting (Az)</label>
              <select className="form-input"
                value={s.az} onChange={(e) => updateString(i, "az", Number(e.target.value))}
                style={{ width: 140 }}>
                {AZ_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <label style={{ fontSize: 10, color: "var(--text-muted)" }}>Helling (°)</label>
              <input className="form-input" type="number" min="0" max="90" placeholder="35"
                value={s.dec} onChange={(e) => updateString(i, "dec", e.target.value)}
                style={{ width: 72 }} />
            </div>
            {strings.length > 1 && (
              <button className="btn btn-ghost btn-sm" style={{ color: "var(--red)", alignSelf: "flex-end" }}
                onClick={() => removeString(i)}>✕</button>
            )}
          </div>
        ))}
        <button className="btn btn-ghost btn-sm" onClick={addString}>+ String toevoegen</button>
      </div>

      {/* Save */}
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
