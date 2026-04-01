import { useState, useEffect, useCallback } from "react";

// ---------------------------------------------------------------------------
// Sensor selector (per device, lazy-loaded)
// ---------------------------------------------------------------------------

const GROUP_ORDER = ["Vermogen", "Spanning", "Stroom", "Totalen", "Gas", "Water", "Batterij", "Overig"];

function SensorSelector({ device, onClose, onSaved }) {
  const [sensors,  setSensors]  = useState([]);
  const [selected, setSelected] = useState(new Set(device.selected_sensors || []));
  const [loading,  setLoading]  = useState(true);
  const [saving,   setSaving]   = useState(false);
  const [error,    setError]    = useState(null);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/homewizard/devices/${device.id}/discover`)
      .then((r) => r.json())
      .then((d) => {
        if (d.error) throw new Error(d.error);
        setSensors(d.sensors);
        setSelected(new Set(d.sensors.filter((s) => s.selected).map((s) => s.key)));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [device.id]);

  const toggle = (key) =>
    setSelected((prev) => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/homewizard/devices/${device.id}/sensors`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sensors: Array.from(selected) }),
      });
      if (!res.ok) throw new Error("Opslaan mislukt.");
      onSaved(device.id, Array.from(selected));
      onClose();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  const grouped = {};
  for (const s of sensors) {
    if (!grouped[s.group]) grouped[s.group] = [];
    grouped[s.group].push(s);
  }
  const groupKeys = GROUP_ORDER.filter((g) => grouped[g]);

  const fmtVal = (v, unit) => {
    if (v == null) return "";
    if (unit === "W") return `${Math.round(v)} W`;
    if (unit === "kWh") return `${v.toFixed(3)} kWh`;
    if (unit === "m³") return `${v.toFixed(3)} m³`;
    if (unit === "V") return `${v.toFixed(1)} V`;
    if (unit === "A") return `${v.toFixed(2)} A`;
    return unit ? `${v} ${unit}` : `${v}`;
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 520, maxHeight: "80vh", display: "flex", flexDirection: "column" }}
        onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">Sensoren — {device.name}</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div style={{ overflowY: "auto", flex: 1, padding: "0 20px 4px" }}>
          {loading && <div style={{ padding: 20, color: "var(--text-muted)", fontSize: 13 }}>Ophalen…</div>}
          {error   && <div className="form-error" style={{ margin: "12px 0" }}>{error}</div>}
          {!loading && !error && groupKeys.length === 0 && (
            <div style={{ padding: 20, color: "var(--text-muted)", fontSize: 13 }}>
              Geen numerieke sensoren gevonden op dit apparaat.
            </div>
          )}
          {groupKeys.map((group) => (
            <div key={group} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)",
                textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 6 }}>{group}</div>
              {grouped[group].map((s) => (
                <label key={s.key} className="hw-sensor-check-row">
                  <input type="checkbox" checked={selected.has(s.key)} onChange={() => toggle(s.key)} />
                  <span className="hw-sensor-check-label">{s.label}</span>
                  <span className="hw-sensor-check-val">{fmtVal(s.value, s.unit)}</span>
                </label>
              ))}
            </div>
          ))}
        </div>
        <div className="modal-footer">
          {error && <div className="form-error" style={{ flex: 1 }}>{error}</div>}
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Annuleren</button>
          <button className="btn btn-primary btn-sm" onClick={save} disabled={saving || loading}>
            {saving ? "Opslaan…" : `Opslaan (${selected.size} geselecteerd)`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subnet scan dialog
// ---------------------------------------------------------------------------

function ScanDialog({ onClose, onAdd, existingIps }) {
  const [subnet,   setSubnet]   = useState("");
  const [scanning, setScanning] = useState(false);
  const [results,  setResults]  = useState(null);
  const [error,    setError]    = useState(null);
  const [adding,   setAdding]   = useState({});  // ip → true

  // Load default subnet on mount
  useEffect(() => {
    fetch("/api/homewizard/localsubnet")
      .then((r) => r.json())
      .then((d) => setSubnet(d.subnet || "192.168.1.0/24"))
      .catch(() => setSubnet("192.168.1.0/24"));
  }, []);

  const scan = async () => {
    setScanning(true); setError(null); setResults(null);
    try {
      const r = await fetch(`/api/homewizard/scan?subnet=${encodeURIComponent(subnet)}`);
      if (!r.ok) {
        // Try to parse error from JSON, fall back to status text
        let detail = `HTTP ${r.status}`;
        try { const d = await r.json(); if (d.error) detail = d.error; } catch {}
        throw new Error(detail);
      }
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      setResults(d.found ?? []);
    } catch (e) { setError(e.message); }
    finally { setScanning(false); }
  };

  const add = async (device) => {
    setAdding((p) => ({ ...p, [device.ip]: true }));
    try {
      const res = await fetch("/api/homewizard/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: device.ip,
          api_version: device.api_version,
        }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Toevoegen mislukt.");
      onAdd(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setAdding((p) => ({ ...p, [device.ip]: false }));
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">🔍 Netwerk scannen</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div style={{ padding: "16px 20px" }}>
          <div className="settings-row-desc" style={{ marginBottom: 12 }}>
            Scant het subnet op HomeWizard apparaten. Vereist dat "Lokale API" ingeschakeld is
            in de HomeWizard app.
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <input className="form-input" value={subnet}
              onChange={(e) => setSubnet(e.target.value)}
              placeholder="bijv. 192.168.1.0/24"
              style={{ flex: 1 }}
              onKeyDown={(e) => e.key === "Enter" && scan()} />
            <button className="btn btn-primary btn-sm" onClick={scan} disabled={scanning}>
              {scanning ? "Scannen…" : "Scannen"}
            </button>
          </div>
          {scanning && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text-muted)", fontSize: 13 }}>
              <div className="loading-spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
              Subnet scannen… (dit duurt enkele seconden)
            </div>
          )}
          {error && <div className="form-error">{error}</div>}
          {results !== null && (
            results.length === 0 ? (
              <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
                Geen HomeWizard apparaten gevonden op {subnet}.
              </div>
            ) : (
              <div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}>
                  {results.length} apparaat{results.length !== 1 ? "en" : ""} gevonden:
                </div>
                {results.map((dev) => {
                  const alreadyAdded = existingIps.includes(dev.ip);
                  return (
                    <div key={dev.ip} style={{ display: "flex", alignItems: "center", gap: 10,
                      padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
                          {dev.product_name || dev.product_type || "HomeWizard"}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "monospace" }}>
                          {dev.ip}
                          {dev.product_type && <span style={{ marginLeft: 8 }}>{dev.product_type}</span>}
                          <span style={{ marginLeft: 8 }}>API v{dev.api_version === 2 ? "2" : "1"}</span>
                          {dev.firmware_version && <span style={{ marginLeft: 8 }}>fw {dev.firmware_version}</span>}
                        </div>
                      </div>
                      {alreadyAdded ? (
                        <span style={{ fontSize: 12, color: "var(--green)" }}>✓ Toegevoegd</span>
                      ) : (
                        <button className="btn btn-primary btn-sm"
                          onClick={() => add(dev)}
                          disabled={adding[dev.ip]}>
                          {adding[dev.ip] ? "…" : "+ Toevoegen"}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )
          )}
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Sluiten</button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// v2 pairing dialog
// ---------------------------------------------------------------------------

function PairDialog({ device, onClose, onPaired }) {
  const [pairing, setPairing] = useState(false);
  const [error,   setError]   = useState(null);
  const [success, setSuccess] = useState(false);
  const [secs,    setSecs]    = useState(null);  // countdown

  const pair = async () => {
    setPairing(true); setError(null);
    // Start 30s countdown
    let t = 30;
    setSecs(t);
    const tick = setInterval(() => { t--; setSecs(t); if (t <= 0) clearInterval(tick); }, 1000);
    try {
      const res = await fetch(`/api/homewizard/devices/${device.id}/pair`, { method: "POST" });
      clearInterval(tick); setSecs(null);
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Koppelen mislukt.");
      setSuccess(true);
      onPaired(device.id);
    } catch (e) {
      clearInterval(tick); setSecs(null);
      setError(e.message);
    } finally {
      setPairing(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 440 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">🔐 API v2 koppelen — {device.name}</div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div style={{ padding: "16px 20px" }}>
          {success ? (
            <div style={{ color: "var(--green)", fontSize: 14 }}>
              ✅ Gekoppeld! API v2 token opgeslagen.
            </div>
          ) : (
            <>
              <div className="settings-row-desc" style={{ marginBottom: 14 }}>
                API v2 vereist een eenmalige koppeling via de knop op het apparaat:
                <ol style={{ margin: "10px 0 0 16px", lineHeight: 1.8 }}>
                  <li>Klik op <strong>"Koppelen starten"</strong></li>
                  <li>Druk <strong>binnen 30 seconden</strong> de knop op je HomeWizard apparaat in</li>
                  <li>Het token wordt automatisch opgeslagen</li>
                </ol>
              </div>
              {error && <div className="form-error" style={{ marginBottom: 10 }}>{error}</div>}
              {secs !== null && (
                <div style={{ fontSize: 13, color: "var(--amber)", marginBottom: 10 }}>
                  ⏱ Druk nu de knop in… ({secs}s)
                </div>
              )}
              <button className="btn btn-primary btn-sm" onClick={pair} disabled={pairing}>
                {pairing ? `Wachten op knop… (${secs ?? ""}s)` : "Koppelen starten"}
              </button>
            </>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            {success ? "Sluiten" : "Annuleren"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HomeWizard settings section
// ---------------------------------------------------------------------------

export default function HomeWizardSettings() {
  const [devices,    setDevices]    = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [addIp,      setAddIp]      = useState("");
  const [addName,    setAddName]    = useState("");
  const [adding,     setAdding]     = useState(false);
  const [addError,   setAddError]   = useState(null);
  const [confirmDel, setConfirmDel] = useState(null);
  const [selectorDev, setSelectorDev] = useState(null);
  const [showScan,   setShowScan]   = useState(false);
  const [pairDev,    setPairDev]    = useState(null);

  const load = useCallback(() => {
    fetch("/api/homewizard/devices")
      .then((r) => r.json())
      .then(setDevices)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!addIp.trim()) { setAddError("IP-adres is vereist."); return; }
    setAdding(true); setAddError(null);
    try {
      const res = await fetch("/api/homewizard/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: addIp.trim(), name: addName.trim() }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || "Toevoegen mislukt.");
      setDevices((p) => [...p, d]);
      setAddIp(""); setAddName("");
    } catch (e) { setAddError(e.message); }
    finally { setAdding(false); }
  };

  const remove = async (id) => {
    await fetch(`/api/homewizard/devices/${id}`, { method: "DELETE" });
    setDevices((p) => p.filter((d) => d.id !== id));
    setConfirmDel(null);
  };

  const handleSensorsSaved = (id, sensors) =>
    setDevices((p) => p.map((d) => d.id === id ? { ...d, selected_sensors: sensors } : d));

  const handlePaired = (id) =>
    setDevices((p) => p.map((d) => d.id === id ? { ...d, api_version: 2 } : d));

  const handleScanAdd = (dev) => {
    setDevices((p) => [...p, dev]);
  };

  const existingIps = devices.map((d) => d.ip);

  return (
    <>
      <div className="settings-section">
        <div className="settings-section-title">🏠 HomeWizard apparaten</div>

        <div className="settings-device-list">
          {loading && (
            <div style={{ padding: "16px 20px", color: "var(--text-muted)", fontSize: 13 }}>Laden…</div>
          )}
          {!loading && devices.length === 0 && (
            <div style={{ padding: "16px 20px", color: "var(--text-muted)", fontSize: 13 }}>
              Nog geen HomeWizard apparaten. Gebruik "Scannen" of voeg handmatig toe via IP.
              <br />
              <span style={{ fontSize: 11 }}>Vereist: "Lokale API" ingeschakeld in de HomeWizard app
                (Instellingen → Meters → … → Lokale API).</span>
            </div>
          )}
          {devices.map((d) => (
            <div key={d.id} className="settings-device-row">
              <div className="settings-device-info">
                <div className="settings-device-name">
                  {d.name}
                  {d.api_version === 2 && (
                    <span style={{ marginLeft: 6, fontSize: 10, padding: "1px 5px",
                      background: "rgba(34,197,94,.15)", color: "var(--green)",
                      borderRadius: 4, border: "1px solid rgba(34,197,94,.3)" }}>v2</span>
                  )}
                </div>
                <div className="settings-device-ip">
                  {d.ip}
                  {d.product_type && <span style={{ marginLeft: 8, opacity: .6 }}>· {d.product_type}</span>}
                  {d.selected_sensors?.length > 0 &&
                    <span style={{ marginLeft: 8, color: "var(--green)", fontSize: 11 }}>
                      {d.selected_sensors.length} sensor{d.selected_sensors.length !== 1 ? "s" : ""}
                    </span>
                  }
                  {d.api_version === 2 && !d.token &&
                    <span style={{ marginLeft: 8, color: "var(--amber)", fontSize: 11 }}>⚠ Niet gekoppeld</span>
                  }
                </div>
              </div>
              <div className="settings-device-actions">
                {d.api_version === 2 && !d.token && (
                  <button className="btn btn-ghost btn-sm" style={{ color: "var(--amber)" }}
                    onClick={() => setPairDev(d)}>🔐 Koppelen</button>
                )}
                <button className="btn btn-ghost btn-sm" onClick={() => setSelectorDev(d)}>☑ Sensoren</button>
                {confirmDel === d.id ? (
                  <>
                    <button className="btn btn-danger btn-sm" onClick={() => remove(d.id)}>Bevestigen</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDel(null)}>Annuleren</button>
                  </>
                ) : (
                  <button className="btn btn-ghost btn-sm" style={{ color: "var(--red)" }}
                    onClick={() => setConfirmDel(d.id)}>✕</button>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Add / scan bar */}
        <div style={{ padding: "12px 20px", borderTop: "1px solid var(--border)" }}>
          {addError && <div className="form-error" style={{ marginBottom: 8 }}>{addError}</div>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input className="form-input" style={{ flex: "1 1 150px" }}
              placeholder="IP-adres"
              value={addIp} onChange={(e) => setAddIp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()} />
            <input className="form-input" style={{ flex: "1 1 130px" }}
              placeholder="Naam (optioneel)"
              value={addName} onChange={(e) => setAddName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()} />
            <button className="btn btn-primary btn-sm" onClick={add} disabled={adding}>
              {adding ? "…" : "+ Handmatig"}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowScan(true)}>
              🔍 Scannen
            </button>
          </div>
        </div>
      </div>

      {showScan && (
        <ScanDialog
          onClose={() => setShowScan(false)}
          onAdd={(dev) => { handleScanAdd(dev); }}
          existingIps={existingIps}
        />
      )}
      {selectorDev && (
        <SensorSelector device={selectorDev} onClose={() => setSelectorDev(null)} onSaved={handleSensorsSaved} />
      )}
      {pairDev && (
        <PairDialog device={pairDev} onClose={() => setPairDev(null)} onPaired={handlePaired} />
      )}
    </>
  );
}
