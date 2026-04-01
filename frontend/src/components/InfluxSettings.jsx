/**
 * InfluxSettings – InfluxDB connection scanner, browser, and slot mapper.
 * bat_soc and bat_w support multiple entries (one per battery).
 */
import { useState, useEffect } from "react";

// ── Slot definitions ──────────────────────────────────────────────────────
// multi:true  → stored as array, user can add/remove rows
const SLOTS = [
  { key: "house_w", label: "Thuisverbruik",      icon: "🏠", unit: "W", desc: "Vermogen verbruikt door het huis", multi: false },
  { key: "solar_w", label: "Zonnepanelen",        icon: "☀️", unit: "W", desc: "Opgewekt vermogen (totaal)",        multi: false },
  { key: "net_w",   label: "Net (import/export)", icon: "⚡", unit: "W", desc: "Positief = afname, negatief = injectie", multi: false },
  { key: "bat_soc", label: "Batterij SOC",        icon: "🔋", unit: "%", desc: "Laadtoestand per batterij (gemiddelde)", multi: true },
  { key: "bat_w",   label: "Batterij vermogen",   icon: "🔌", unit: "W", desc: "Vermogen per batterij (som)",       multi: true },
];

// ── Helpers ───────────────────────────────────────────────────────────────

function Badge({ ok, text }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 12,
      background: ok ? "rgba(74,222,128,0.15)" : "rgba(248,113,113,0.15)",
      color: ok ? "#4ade80" : "#f87171",
    }}>{text}</span>
  );
}

function Section({ title, children }) {
  return (
    <div className="influx-section">
      <div className="influx-section-title">{title}</div>
      {children}
    </div>
  );
}

// ── Single mapping entry row ──────────────────────────────────────────────

function EntryRow({ entry, index, fieldOptions, tags, database, measurement, onChange, onRemove, showRemove, label }) {
  const upd = (patch) => onChange({ ...entry, ...patch, database, measurement });
  return (
    <div className="influx-entry-row">
      {label && <span className="influx-entry-label">{label}</span>}

      {/* Field */}
      <select className="form-input form-input-sm" value={entry.field || ""}
        onChange={(e) => upd({ field: e.target.value })}
        title="Veld">
        <option value="">— veld —</option>
        {fieldOptions.map((f) => <option key={f} value={f}>{f}</option>)}
      </select>

      {/* Tag filter */}
      {tags && tags.length > 0 && (
        <>
          <select className="form-input form-input-sm" style={{ width: 100 }}
            value={entry.tag_key || ""}
            onChange={(e) => upd({ tag_key: e.target.value, tag_value: e.target.value ? entry.tag_value : "" })}
            title="Tag (optioneel)">
            <option value="">— tag —</option>
            {tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          {entry.tag_key && (
            <input className="form-input form-input-sm" style={{ width: 190 }}
              placeholder="tag waarde (bv. sensor.bat1_soc)"
              value={entry.tag_value || ""}
              onChange={(e) => upd({ tag_value: e.target.value })} />
          )}
        </>
      )}

      {/* Invert */}
      {entry.field && (
        <label className="influx-invert-toggle" title="Waarden omgekeerd (×-1)">
          <input type="checkbox" checked={!!entry.invert}
            onChange={(e) => upd({ invert: e.target.checked })} />
          <span>omk.</span>
        </label>
      )}

      {/* Scale */}
      {entry.field && (
        <input className="form-input form-input-sm" type="number" step="0.001" style={{ width: 68 }}
          title="Schaalfactor (bv. 1000 als waarde in kW staat)"
          placeholder="×schaal"
          value={entry.scale ?? 1}
          onChange={(e) => upd({ scale: parseFloat(e.target.value) || 1 })} />
      )}

      {showRemove && (
        <button className="btn btn-ghost btn-xs" onClick={onRemove} title="Verwijder">✕</button>
      )}
    </div>
  );
}

// ── Full slot block (single or multi) ────────────────────────────────────

function SlotBlock({ slot, fieldOptions, tags, database, measurement, value, onChange }) {
  // value: object (single) or array (multi)
  const entries = slot.multi
    ? (Array.isArray(value) ? value : (value?.field ? [value] : []))
    : null;

  const updateSingle = (patch) => onChange({ ...(value || {}), ...patch, database, measurement });

  const updateEntry = (i, updated) => {
    const arr = [...entries];
    arr[i] = updated;
    onChange(arr);
  };

  const addEntry = () => onChange([...entries, { field: "", database, measurement }]);

  const removeEntry = (i) => {
    const arr = entries.filter((_, idx) => idx !== i);
    onChange(arr.length ? arr : []);
  };

  const assigned = slot.multi
    ? entries.some((e) => e.field)
    : !!(value?.field);

  return (
    <div className={`influx-slot-row ${assigned ? "assigned" : ""}`}>
      {/* Slot header */}
      <div className="influx-slot-label">
        <span style={{ fontSize: 18 }}>{slot.icon}</span>
        <div>
          <div style={{ fontWeight: 600, fontSize: 12 }}>{slot.label}</div>
          <div style={{ fontSize: 10, color: "var(--text-dim)" }}>{slot.desc}</div>
        </div>
        {slot.multi && (
          <button className="btn btn-ghost btn-xs" style={{ marginLeft: 8 }} onClick={addEntry}>
            + batterij
          </button>
        )}
      </div>

      {/* Single entry */}
      {!slot.multi && (
        <div className="influx-slot-controls">
          <select className="form-input form-input-sm"
            value={value?.field || ""}
            onChange={(e) => updateSingle({ field: e.target.value })}
            title="Veld">
            <option value="">— veld —</option>
            {fieldOptions.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>

          {tags && tags.length > 0 && (
            <>
              <select className="form-input form-input-sm" style={{ width: 100 }}
                value={value?.tag_key || ""}
                onChange={(e) => updateSingle({ tag_key: e.target.value, tag_value: "" })}
                title="Tag (optioneel)">
                <option value="">— tag —</option>
                {tags.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              {value?.tag_key && (
                <input className="form-input form-input-sm" style={{ width: 190 }}
                  placeholder="tag waarde"
                  value={value?.tag_value || ""}
                  onChange={(e) => updateSingle({ tag_value: e.target.value })} />
              )}
            </>
          )}

          {value?.field && (
            <>
              <label className="influx-invert-toggle">
                <input type="checkbox" checked={!!value?.invert}
                  onChange={(e) => updateSingle({ invert: e.target.checked })} />
                <span>omk.</span>
              </label>
              <input className="form-input form-input-sm" type="number" step="0.001" style={{ width: 68 }}
                placeholder="×schaal" title="Schaalfactor"
                value={value?.scale ?? 1}
                onChange={(e) => updateSingle({ scale: parseFloat(e.target.value) || 1 })} />
              <button className="btn btn-ghost btn-xs" onClick={() => onChange({})} title="Verwijder">✕</button>
            </>
          )}

          {assigned && (
            <div className="influx-slot-summary">
              {database}.{measurement}.{value?.field}
              {value?.tag_key && value?.tag_value ? ` WHERE ${value.tag_key}="${value.tag_value}"` : ""}
              {value?.invert ? " ×-1" : ""}
              {value?.scale && value.scale !== 1 ? ` ×${value.scale}` : ""}
            </div>
          )}
        </div>
      )}

      {/* Multi entries */}
      {slot.multi && (
        <div className="influx-multi-entries">
          {entries.length === 0 && (
            <div style={{ fontSize: 11, color: "var(--text-dim)", padding: "4px 0 4px 24px" }}>
              Geen batterijen — klik "+ batterij"
            </div>
          )}
          {entries.map((entry, i) => (
            <EntryRow key={i} entry={entry} index={i}
              label={`Batterij ${i + 1}`}
              fieldOptions={fieldOptions} tags={tags}
              database={database} measurement={measurement}
              onChange={(updated) => updateEntry(i, updated)}
              onRemove={() => removeEntry(i)}
              showRemove={entries.length > 1 || entry.field} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Slot mapper panel ─────────────────────────────────────────────────────

function SlotMapper({ fields, tags, database, measurement, mappings, onChange }) {
  const fieldOptions = (fields || []).map((f) => f.key || f);

  const updateSlot = (key, value) => onChange({ ...mappings, [key]: value });

  return (
    <div className="influx-mapper">
      <div className="influx-mapper-header">
        📌 Koppel velden aan energieslots
        <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 8 }}>
          {database} → {measurement}
        </span>
      </div>
      {SLOTS.map((slot) => (
        <SlotBlock key={slot.key}
          slot={slot}
          fieldOptions={fieldOptions}
          tags={tags}
          database={database}
          measurement={measurement}
          value={mappings[slot.key] ?? (slot.multi ? [] : {})}
          onChange={(v) => updateSlot(slot.key, v)}
        />
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────

export default function InfluxSettings() {
  const [conn,     setConn]     = useState({ url: "", version: "auto", username: "", password: "", token: "", org: "" });
  const [scanning, setScanning] = useState(false);
  const [error,    setError]    = useState(null);
  const [result,   setResult]   = useState(null);
  const [selectedDb,   setSelectedDb]   = useState(null);
  const [selectedMeas, setSelectedMeas] = useState(null);
  const [measResult,   setMeasResult]   = useState(null);
  const [fieldResult,  setFieldResult]  = useState(null);
  const [mappings, setMappings] = useState({});
  const [saving,   setSaving]   = useState(false);
  const [saveOk,   setSaveOk]   = useState(false);

  useEffect(() => {
    fetch("/api/influx/connection")
      .then((r) => r.json())
      .then((d) => setConn((p) => ({ ...p, ...d })))
      .catch(() => {});
    fetch("/api/influx/source")
      .then((r) => r.json())
      .then((d) => {
        if (d.mappings)    setMappings(d.mappings);
        if (d.database)    setSelectedDb(d.database);
        if (d.measurement) setSelectedMeas(d.measurement);
      })
      .catch(() => {});
  }, []);

  const set = (k, v) => setConn((p) => ({ ...p, [k]: v }));

  const scan = async () => {
    setScanning(true); setError(null); setResult(null);
    setSelectedDb(null); setSelectedMeas(null); setMeasResult(null); setFieldResult(null);
    try {
      const r = await fetch("/api/influx/scan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(conn),
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      setResult(d);
    } catch (e) { setError(e.message); }
    finally { setScanning(false); }
  };

  const selectDb = async (name) => {
    setSelectedDb(name); setSelectedMeas(null); setMeasResult(null); setFieldResult(null);
    setScanning(true); setError(null);
    try {
      const body = { ...conn, [result?.version === "v2" ? "bucket" : "database"]: name };
      const r = await fetch("/api/influx/scan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      setMeasResult(d);
    } catch (e) { setError(e.message); }
    finally { setScanning(false); }
  };

  const selectMeasurement = async (name) => {
    setSelectedMeas(name); setFieldResult(null);
    setScanning(true); setError(null);
    try {
      const body = { ...conn,
        [result?.version === "v2" ? "bucket" : "database"]: selectedDb,
        measurement: name,
      };
      const r = await fetch("/api/influx/scan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      setFieldResult(d);
    } catch (e) { setError(e.message); }
    finally { setScanning(false); }
  };

  const save = async () => {
    setSaving(true); setSaveOk(false); setError(null);
    try {
      await fetch("/api/influx/connection", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(conn),
      });
      const r = await fetch("/api/influx/source", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          version:     result?.version || conn.version,
          url:         conn.url,
          database:    selectedDb || "",
          measurement: selectedMeas || "",
          mappings,
        }),
      });
      if (!r.ok) throw new Error("Opslaan mislukt");
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 3000);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  const isV2 = result?.version === "v2";
  const databaseList = isV2
    ? (result?.buckets || []).map((b) => (typeof b === "object" ? b.name : b))
    : (result?.databases || []);

  // Count assigned slots (single fields + multi entries)
  const assignedCount = SLOTS.reduce((n, s) => {
    const v = mappings[s.key];
    if (!v) return n;
    if (s.multi) return n + (Array.isArray(v) ? v.filter((e) => e.field).length : 0);
    return n + (v.field ? 1 : 0);
  }, 0);

  return (
    <div className="settings-section">
      <div className="settings-section-title">🗄️ InfluxDB verbinding &amp; databronnen</div>

      {/* Connection form */}
      <div className="influx-form">
        <div className="influx-form-row">
          <label>URL</label>
          <input className="form-input" style={{ flex: 1 }}
            placeholder="http://192.168.1.x:8086"
            value={conn.url} onChange={(e) => set("url", e.target.value)} />
        </div>
        <div className="influx-form-row">
          <label>Versie</label>
          <select className="form-input" style={{ width: 130 }}
            value={conn.version} onChange={(e) => set("version", e.target.value)}>
            <option value="auto">Auto-detect</option>
            <option value="v1">InfluxDB v1</option>
            <option value="v2">InfluxDB v2</option>
          </select>
        </div>
        {conn.version !== "v2" && (
          <>
            <div className="influx-form-row">
              <label>Gebruikersnaam</label>
              <input className="form-input" style={{ width: 200 }}
                placeholder="admin (leeg = geen auth)"
                value={conn.username} onChange={(e) => set("username", e.target.value)} />
            </div>
            <div className="influx-form-row">
              <label>Wachtwoord</label>
              <input className="form-input" type="password" style={{ width: 200 }}
                value={conn.password} onChange={(e) => set("password", e.target.value)} />
            </div>
          </>
        )}
        {conn.version !== "v1" && (
          <>
            <div className="influx-form-row">
              <label>Token</label>
              <input className="form-input" style={{ flex: 1 }}
                placeholder="API token (InfluxDB v2)"
                value={conn.token} onChange={(e) => set("token", e.target.value)} />
            </div>
            <div className="influx-form-row">
              <label>Organisatie</label>
              <input className="form-input" style={{ width: 200 }}
                placeholder="org naam (v2)"
                value={conn.org} onChange={(e) => set("org", e.target.value)} />
            </div>
          </>
        )}
        <div className="influx-form-actions">
          <button className="btn btn-primary btn-sm" onClick={scan} disabled={scanning || !conn.url}>
            {scanning ? "Scannen…" : "🔍 Verbinden & Scannen"}
          </button>
          <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}
            style={{ background: "var(--green)", borderColor: "var(--green)" }}>
            {saving ? "Opslaan…" : `💾 Opslaan${assignedCount > 0 ? ` (${assignedCount} slots)` : ""}`}
          </button>
          {saveOk && <span style={{ fontSize: 12, color: "var(--green)" }}>✓ Opgeslagen</span>}
        </div>
      </div>

      {error && (
        <div className="forecast-error" style={{ margin: "8px 20px 0" }}>
          <span style={{ fontWeight: 600 }}>⚠ </span>{error}
        </div>
      )}

      {/* Saved mapping summary (when not scanning) */}
      {assignedCount > 0 && !result && (
        <div className="influx-saved-summary">
          <div style={{ fontSize: 11, color: "var(--text-dim)", fontWeight: 700, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
            Opgeslagen koppeling — {selectedDb} → {selectedMeas}
          </div>
          {SLOTS.map((s) => {
            const v = mappings[s.key];
            if (!v) return null;
            if (s.multi) {
              const entries = Array.isArray(v) ? v.filter((e) => e.field) : [];
              if (!entries.length) return null;
              return entries.map((e, i) => (
                <div key={`${s.key}-${i}`} className="influx-summary-row">
                  <span>{s.icon} {s.label} {entries.length > 1 ? i + 1 : ""}</span>
                  <span className="influx-summary-mapping">
                    {e.field}{e.tag_key && e.tag_value ? ` WHERE ${e.tag_key}="${e.tag_value}"` : ""}{e.invert ? " ×-1" : ""}
                  </span>
                </div>
              ));
            }
            if (!v.field) return null;
            return (
              <div key={s.key} className="influx-summary-row">
                <span>{s.icon} {s.label}</span>
                <span className="influx-summary-mapping">
                  {v.field}{v.tag_key && v.tag_value ? ` WHERE ${v.tag_key}="${v.tag_value}"` : ""}{v.invert ? " ×-1" : ""}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Browser */}
      {result && (
        <div className="influx-browser">
          <div className="influx-browser-header">
            <Badge ok text={`InfluxDB ${result.version?.toUpperCase()}`} />
            {result.orgs?.length > 0 && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                orgs: {result.orgs.join(", ")}
              </span>
            )}
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-dim)" }}>
              Klik om in te zoomen →
            </span>
          </div>

          <div className="influx-browser-cols">
            {/* Column 1: Databases / Buckets */}
            <Section title={isV2 ? "Buckets" : "Databases"}>
              {databaseList.length === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-muted)", padding: 8 }}>Geen of onvoldoende rechten.</div>
              )}
              {databaseList.map((name) => (
                <button key={name}
                  className={`influx-list-item ${selectedDb === name ? "selected" : ""}`}
                  onClick={() => selectDb(name)}>
                  <span className="influx-list-icon">{isV2 ? "🪣" : "🗄️"}</span>
                  <span className="influx-list-name">{name}</span>
                  {selectedDb === name && <span style={{ marginLeft: "auto", fontSize: 10 }}>▶</span>}
                </button>
              ))}
            </Section>

            {/* Column 2: Measurements */}
            {measResult && (
              <Section title={`Measurements (${measResult.measurements?.length ?? 0})`}>
                {(measResult.measurements || []).length === 0 && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", padding: 8 }}>Leeg.</div>
                )}
                {(measResult.measurements || []).map((m) => (
                  <button key={m}
                    className={`influx-list-item ${selectedMeas === m ? "selected" : ""}`}
                    onClick={() => selectMeasurement(m)} title={m}>
                    <span className="influx-list-icon">📋</span>
                    <span className="influx-list-name">{m}</span>
                    {selectedMeas === m && <span style={{ marginLeft: "auto", fontSize: 10 }}>▶</span>}
                  </button>
                ))}
                {measResult.retention_policies?.length > 0 && (
                  <div style={{ marginTop: 8, padding: "0 8px" }}>
                    <div style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1 }}>Retention</div>
                    {measResult.retention_policies.map((rp, i) => (
                      <div key={i} style={{ fontSize: 11, color: "var(--text-muted)", padding: "2px 0" }}>
                        {rp.name} · {rp.duration || "∞"}
                      </div>
                    ))}
                  </div>
                )}
              </Section>
            )}

            {/* Column 3: Fields */}
            {fieldResult && (
              <Section title={`Velden — ${selectedMeas}`}>
                {(fieldResult.fields || []).length === 0 && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", padding: 8 }}>Geen velden.</div>
                )}
                <div>
                  <div style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1, padding: "4px 10px 2px" }}>Fields</div>
                  {(fieldResult.fields || []).map((f) => {
                    const key = f.key || f;
                    const usedBy = SLOTS.filter((s) => {
                      const v = mappings[s.key];
                      if (!v) return false;
                      if (s.multi) return Array.isArray(v) && v.some((e) => e.field === key && e.measurement === selectedMeas);
                      return v.field === key && v.measurement === selectedMeas;
                    });
                    return (
                      <div key={key} className="influx-field-row">
                        <span className="influx-field-key">{key}</span>
                        <span className="influx-field-type">{f.type || ""}</span>
                        {usedBy.length > 0 && (
                          <span style={{ marginLeft: 6, fontSize: 9, color: "var(--accent)" }}>
                            {usedBy.map((s) => s.icon).join("")}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
                {(fieldResult.tags || []).length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1, padding: "4px 10px 2px" }}>Tags</div>
                    {(fieldResult.tags || []).map((t) => (
                      <div key={t} className="influx-field-row">
                        <span className="influx-field-key" style={{ color: "#a78bfa" }}>🏷 {t}</span>
                      </div>
                    ))}
                  </div>
                )}
                {fieldResult.sample?.length > 0 && (
                  <div style={{ marginTop: 8, padding: "0 6px" }}>
                    <div style={{ fontSize: 10, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1, padding: "2px 4px" }}>Steekproef</div>
                    <div style={{ overflowX: "auto" }}>
                      <table className="influx-sample-table">
                        <thead>
                          <tr>{Object.keys(fieldResult.sample[0]).map((k) => <th key={k}>{k}</th>)}</tr>
                        </thead>
                        <tbody>
                          {fieldResult.sample.map((row, i) => (
                            <tr key={i}>
                              {Object.values(row).map((v, j) => (
                                <td key={j}>{v == null ? "—" : String(v).slice(0, 40)}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </Section>
            )}
          </div>

          {/* Slot mapper — shown when a measurement is selected */}
          {fieldResult && selectedDb && selectedMeas && (
            <SlotMapper
              fields={fieldResult.fields || []}
              tags={fieldResult.tags || []}
              database={selectedDb}
              measurement={selectedMeas}
              mappings={mappings}
              onChange={setMappings}
            />
          )}
        </div>
      )}
    </div>
  );
}
