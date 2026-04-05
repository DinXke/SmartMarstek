import { useState, useEffect, useRef } from "react";

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

  // ── Actual solar source ──────────────────────────────────────────────────
  const [updateInterval, setUpdateInterval] = useState(900);       // seconds
  const [actualSource,   setActualSource]   = useState("none");   // "none"|"influx"|"ha"
  const [actualEntityId, setActualEntityId] = useState("");
  const [haEntities,     setHaEntities]     = useState([]);
  const [entitySearch,   setEntitySearch]   = useState("");
  const [entityOpen,     setEntityOpen]     = useState(false);
  const entityRef = useRef(null);

  useEffect(() => {
    const close = (e) => { if (entityRef.current && !entityRef.current.contains(e.target)) setEntityOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  useEffect(() => {
    fetch("api/forecast/settings")
      .then((r) => r.json())
      .then((d) => {
        setConfigured(d.configured);
        setHint(d.apiKeyHint || "");
        if (d.lat) setLat(String(d.lat));
        if (d.lon) setLon(String(d.lon));
        if (d.strings && d.strings.length) setStrings(d.strings);
      if (d.update_interval) setUpdateInterval(d.update_interval);
      })
      .catch(() => {});
    fetch("api/forecast/actual-source")
      .then((r) => r.json())
      .then((d) => { setActualSource(d.source || "none"); setActualEntityId(d.entity_id || ""); })
      .catch(() => {});
    fetch("api/ha/entities")
      .then((r) => r.json())
      .then((d) => setHaEntities(d.entities ?? []))
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
      body.update_interval = parseInt(updateInterval);
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const r = await fetch("api/forecast/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("Opslaan mislukt");
      if (apiKey.trim()) { setConfigured(true); setHint(`…${apiKey.trim().slice(-4)}`); setApiKey(""); }
      await fetch("api/forecast/actual-source", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: actualSource, entity_id: actualEntityId }),
      });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (e) { setError(e.message); }
    finally     { setSaving(false); }
  };

  const filteredEntities = haEntities.filter((e) =>
    !entitySearch ||
    e.friendly_name?.toLowerCase().includes(entitySearch.toLowerCase()) ||
    e.entity_id.toLowerCase().includes(entitySearch.toLowerCase())
  );

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

      {/* Update interval */}
      <div className="settings-row">
        <div>
          <div className="settings-row-label">Verversingsfrequentie</div>
          <div className="settings-row-desc">Hoe vaak de voorspelling opgehaald wordt van forecast.solar.</div>
        </div>
        <select className="form-input" value={updateInterval} onChange={(e) => setUpdateInterval(e.target.value)}
          style={{ width: 160 }}>
          <option value={900}>15 minuten</option>
          <option value={1800}>30 minuten</option>
          <option value={3600}>1 uur</option>
          <option value={14400}>4 uur</option>
          <option value={43200}>12 uur</option>
          <option value={86400}>24 uur</option>
        </select>
      </div>

      {/* Actual solar source */}
      <div className="settings-row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
        <div>
          <div className="settings-row-label">Werkelijke opbrengst bron</div>
          <div className="settings-row-desc">
            Overlay van echte zonneopbrengst op de forecast grafiek.
          </div>
        </div>
        <select className="form-input" value={actualSource} onChange={(e) => setActualSource(e.target.value)}
          style={{ width: 320 }}>
          <option value="none">Geen</option>
          <option value="flow">Databronnen (aanbevolen — zelfde als flow dashboard)</option>
          <option value="influx">InfluxDB — zonnepanelen slot</option>
          <option value="ha">Home Assistant entiteit</option>
        </select>
        {actualSource === "flow" && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Gebruikt de solar_power bron uit Bronnen. Werkt met ESPHome, InfluxDB en HA-sensoren.
          </div>
        )}
        {actualSource === "ha" && (
          <div ref={entityRef} style={{ position: "relative", width: "100%", maxWidth: 400 }}>
            <input className="form-input" placeholder="Zoek entiteit…"
              value={entitySearch || actualEntityId}
              onFocus={() => { setEntityOpen(true); setEntitySearch(""); }}
              onChange={(e) => { setEntitySearch(e.target.value); setEntityOpen(true); }}
              style={{ width: "100%" }} />
            {entityOpen && filteredEntities.length > 0 && (
              <div style={{
                position: "absolute", zIndex: 100, background: "var(--bg-card)",
                border: "1px solid var(--border)", borderRadius: 8,
                maxHeight: 220, overflowY: "auto", width: "100%", top: "100%", marginTop: 4,
              }}>
                {filteredEntities.slice(0, 80).map((e) => (
                  <div key={e.entity_id}
                    style={{ padding: "6px 12px", cursor: "pointer", fontSize: 12 }}
                    onMouseDown={() => {
                      setActualEntityId(e.entity_id);
                      setEntitySearch("");
                      setEntityOpen(false);
                    }}>
                    <span style={{ color: "var(--text-primary)" }}>{e.friendly_name || e.entity_id}</span>
                    <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>{e.entity_id}</span>
                    {e.unit && <span style={{ color: "var(--accent)", marginLeft: 6 }}>{e.unit}</span>}
                  </div>
                ))}
              </div>
            )}
            {actualEntityId && !entityOpen && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                Geselecteerd: <span style={{ color: "var(--accent)" }}>{actualEntityId}</span>
              </div>
            )}
          </div>
        )}
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
