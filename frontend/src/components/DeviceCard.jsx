import React, { useState, useEffect, useRef } from "react";
import BatteryGauge from "./BatteryGauge.jsx";
import PowerFlow from "./PowerFlow.jsx";
import { getFlowSettings } from "./SettingsPage.jsx";

// ---------------------------------------------------------------------------
// Entity helpers
// Entity IDs from ESPHome v3 SSE are "domain/Friendly Name"
// e.g. "sensor/Marstek Battery State Of Charge"
// ---------------------------------------------------------------------------

/** Normalize for fuzzy matching: lowercase, collapse punctuation/underscore to space */
const norm = (s) =>
  String(s)
    .toLowerCase()
    .replace(/[_./\\]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/**
 * Find an entity whose name part (after the domain/) contains ALL given terms.
 * Terms are also normalized so underscores/periods are treated as spaces.
 */
const getEntity = (entities, ...terms) => {
  const searchTerms = terms.map(norm);
  const id = Object.keys(entities).find((k) => {
    const slash = k.indexOf("/");
    const namePart = norm(slash >= 0 ? k.substring(slash + 1) : k);
    return searchTerms.every((t) => namePart.includes(t));
  });
  return id ? entities[id] : null;
};

/** Numeric value */
const val = (e) => {
  if (!e) return null;
  if (e.value != null && !isNaN(e.value)) return Number(e.value);
  const n = parseFloat(e.state);
  return isNaN(n) ? null : n;
};

/** String state */
const str = (e) => e?.state ?? null;

// Format helpers
const fmtPower   = (v) => v == null ? "—" : `${Math.round(v)} W`;
const fmtVoltage = (v) => v == null ? "—" : `${v.toFixed(1)} V`;
const fmtCurrent = (v) => v == null ? "—" : `${v.toFixed(1)} A`;
const fmtEnergy  = (v) => v == null ? "—" : `${v.toFixed(2)} kWh`;
const fmtTemp    = (v) => v == null ? "—" : `${v.toFixed(1)} °C`;

function inverterBadgeClass(state) {
  if (!state) return "badge-gray";
  const s = state.toLowerCase();
  if (s === "charge")    return "badge-green";
  if (s === "discharge") return "badge-amber";
  if (s === "fault")     return "badge-red";
  if (s === "bypass" || s === "ac bypass") return "badge-purple";
  return "badge-gray";
}

function tempClass(v) {
  if (v == null) return "temp-normal";
  if (v > 55)   return "temp-hot";
  if (v > 45)   return "temp-warn";
  return "temp-normal";
}

// ---------------------------------------------------------------------------
// Tab components
// ---------------------------------------------------------------------------

function MetricCard({ label, value, icon, color }) {
  return (
    <div className="metric-card">
      {icon && <div className="metric-card-icon">{icon}</div>}
      <div className="metric-card-label">{label}</div>
      <div className="metric-card-value" style={color ? { color } : undefined}>
        {value}
      </div>
    </div>
  );
}

function OverviewTab({ entities }) {
  const acPower  = val(getEntity(entities, "AC Power"));
  const batPower = val(getEntity(entities, "Battery Power"));
  const batVolt  = val(getEntity(entities, "Battery Voltage"));
  const batAmp   = val(getEntity(entities, "Battery Current"));
  const acVolt   = val(getEntity(entities, "AC Voltage"));
  const acAmp    = val(getEntity(entities, "AC Current"));

  const pwrColor = (v) =>
    v == null ? undefined : v > 0 ? "var(--amber)" : v < 0 ? "var(--blue)" : undefined;

  return (
    <div className="metric-grid">
      <MetricCard label="AC Power"       value={fmtPower(acPower)}   icon="⚡" color={pwrColor(acPower)} />
      <MetricCard label="Battery Power"  value={fmtPower(batPower)}  icon="🔋" color={pwrColor(batPower)} />
      <MetricCard label="Battery Voltage" value={fmtVoltage(batVolt)} icon="🔌" />
      <MetricCard label="Battery Current" value={fmtCurrent(batAmp)} icon="➡️"
        color={batAmp != null ? (batAmp > 0 ? "var(--amber)" : "var(--blue)") : undefined} />
      <MetricCard label="AC Voltage"  value={fmtVoltage(acVolt)} icon="🌐" />
      <MetricCard label="AC Current"  value={fmtCurrent(acAmp)}  icon="〰️" />
    </div>
  );
}

function EnergyTab({ entities }) {
  const rows = [
    {
      label: "Daily",
      charge:    val(getEntity(entities, "Daily Charging Energy")),
      discharge: val(getEntity(entities, "Daily Discharging Energy")),
    },
    {
      label: "Monthly",
      charge:    val(getEntity(entities, "Monthly Charging Energy")),
      discharge: val(getEntity(entities, "Monthly Discharging Energy")),
    },
    {
      label: "Total",
      charge:    val(getEntity(entities, "Total Charging Energy")),
      discharge: val(getEntity(entities, "Total Discharging Energy")),
    },
  ];

  return (
    <table className="energy-table">
      <thead>
        <tr><th>Period</th><th>⬆ Charging</th><th>⬇ Discharging</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <td>{r.label}</td>
            <td className="val-charge">{fmtEnergy(r.charge)}</td>
            <td className="val-discharge">{fmtEnergy(r.discharge)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TemperatureTab({ entities }) {
  const temps = [
    { label: "Internal",     entity: getEntity(entities, "Internal Temperature") },
    { label: "MOS 1",        entity: getEntity(entities, "MOS1 Temperature") },
    { label: "MOS 2",        entity: getEntity(entities, "MOS2 Temperature") },
    { label: "Max Cell",     entity: getEntity(entities, "Max", "Cell Temperature") },
    { label: "Min Cell",     entity: getEntity(entities, "Min", "Cell Temperature") },
  ];
  const voltages = [
    { label: "Max Cell V",   entity: getEntity(entities, "Maximum Cell Voltage") },
    { label: "Min Cell V",   entity: getEntity(entities, "Minimum Cell Voltage") },
    { label: "Delta Cell V", entity: getEntity(entities, "Cell Voltage Delta") },
  ];

  return (
    <>
      <div className="section-title">Temperatures</div>
      <div className="temp-grid">
        {temps.map(({ label, entity }) => {
          const v = val(entity);
          return (
            <div className="temp-card" key={label}>
              <div className="temp-card-label">{label}</div>
              <div className={`temp-card-value ${tempClass(v)}`}>{fmtTemp(v)}</div>
            </div>
          );
        })}
      </div>
      <div className="section-title" style={{ marginTop: 16 }}>Cell Voltages</div>
      <div className="temp-grid">
        {voltages.map(({ label, entity }) => {
          const v = val(entity);
          return (
            <div className="temp-card" key={label}>
              <div className="temp-card-label">{label}</div>
              <div className="temp-card-value temp-normal">
                {v == null ? "—" : `${v.toFixed(3)} V`}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function AlarmsTab({ entities }) {
  // ESPHome v3 entity IDs: "binary_sensor/Name"
  const sensors = Object.values(entities).filter((e) =>
    e.id && e.id.startsWith("binary_sensor/")
  );

  if (sensors.length === 0) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "16px 0" }}>
        No alarm sensors found.
      </div>
    );
  }

  const cleanName = (name) =>
    (name || "").replace(/^Marstek\s+/i, "").replace(/^BAT\s+/i, "").trim();

  const alarms = sensors.filter((e) => e.state === "ON");
  const ok     = sensors.filter((e) => e.state !== "ON");

  return (
    <>
      {alarms.length > 0 && (
        <>
          <div className="section-title" style={{ color: "var(--red)" }}>
            Active Alarms ({alarms.length})
          </div>
          <div className="alarm-grid" style={{ marginBottom: 14 }}>
            {alarms.map((e) => (
              <div key={e.id} className="alarm-chip alarm">⚠ {cleanName(e.name)}</div>
            ))}
          </div>
        </>
      )}
      <div className="section-title">All Sensors</div>
      <div className="alarm-grid">
        {[...alarms, ...ok].map((e) => {
          const isAlarm = e.state === "ON";
          return (
            <div key={e.id} className={`alarm-chip ${isAlarm ? "alarm" : "ok"}`}>
              {isAlarm ? "⚠" : "✓"} {cleanName(e.name)}
            </div>
          );
        })}
      </div>
    </>
  );
}

function SelectControl({ label, entity, deviceId }) {
  // Use entity.value (clean option string) preferring over entity.state
  const currentVal = entity?.value ?? entity?.state ?? "";
  const [value, setValue] = useState(currentVal);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const v = entity?.value ?? entity?.state;
    if (v != null) setValue(v);
  }, [entity?.value, entity?.state]);

  if (!entity) return null;

  // entity.id = "select/Marstek User Work Mode"
  const slash  = entity.id.indexOf("/");
  const domain = entity.id.substring(0, slash);
  const name   = entity.id.substring(slash + 1);

  // ESPHome sends "option" (singular). Also accept "options" for resilience.
  const optList = entity.option ?? entity.options;
  const options = Array.isArray(optList) && optList.length
    ? optList
    : [entity.value ?? entity.state].filter(Boolean);

  const handleChange = async (e) => {
    const newVal = e.target.value;
    setValue(newVal);
    setSaving(true);
    try {
      await fetch(`/api/devices/${deviceId}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, name, value: newVal }),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="control-row">
      <span className="control-label">{label}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <select className="control-select" value={value} onChange={handleChange} disabled={saving}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
          {entity.state && !options.includes(entity.state) && (
            <option value={entity.state}>{entity.state}</option>
          )}
        </select>
        {saving && <div className="loading-spinner" style={{ width: 14, height: 14 }} />}
      </div>
    </div>
  );
}

function NumberControl({ label, entity, deviceId, unit }) {
  const [inputVal, setInputVal]   = useState("");
  const [saving, setSaving]       = useState(false);
  const [saved, setSaved]         = useState(false);
  const entityVal = val(entity);

  useEffect(() => {
    if (entityVal != null) setInputVal(String(entityVal));
  }, [entityVal]);

  if (!entity) return null;

  const slash  = entity.id.indexOf("/");
  const domain = entity.id.substring(0, slash);
  const name   = entity.id.substring(slash + 1);
  const min    = entity.min_value ?? 0;
  const max    = entity.max_value ?? 9999;
  const step   = entity.step ?? 1;

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`/api/devices/${deviceId}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, name, value: inputVal }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="control-row">
      <span className="control-label">{label}</span>
      <div className="number-control">
        <input
          type="number"
          className="control-input"
          value={inputVal}
          min={min} max={max} step={step}
          onChange={(e) => setInputVal(e.target.value)}
        />
        {unit && <span className="control-unit">{unit}</span>}
        <button
          className="btn btn-sm btn-primary"
          disabled={saving}
          onClick={handleSave}
          style={saved ? { background: "var(--green)" } : undefined}
        >
          {saving ? "…" : saved ? "✓" : "Set"}
        </button>
      </div>
    </div>
  );
}

function ControlsTab({ entities, deviceId }) {
  const selects = [
    { label: "RS485 Mode",              entity: getEntity(entities, "RS485 Control Mode") },
    { label: "Work Mode",               entity: getEntity(entities, "User Work Mode") },
    { label: "Backup Function",         entity: getEntity(entities, "Backup Function") },
    { label: "Forcible Charge/Discharge", entity: getEntity(entities, "Forcible Charge", "Discharge") },
  ].filter((s) => s.entity);

  const numbers = [
    { label: "Forcible Charge Power",   entity: getEntity(entities, "Forcible Charge Power"),   unit: "W" },
    { label: "Forcible Discharge Power",entity: getEntity(entities, "Forcible Discharge Power"), unit: "W" },
    { label: "Charge To SOC",           entity: getEntity(entities, "Charge To SOC"),            unit: "%" },
    { label: "Max. Charge Power",       entity: getEntity(entities, "Max", "Charge Power"),      unit: "W" },
    { label: "Max. Discharge Power",    entity: getEntity(entities, "Max", "Discharge Power"),   unit: "W" },
  ].filter((n) => n.entity);

  return (
    <div className="controls-section">
      {selects.length > 0 && (
        <div className="controls-group">
          <div className="controls-group-title">Mode Controls</div>
          {selects.map(({ label, entity }) => (
            <SelectControl key={entity.id} label={label} entity={entity} deviceId={deviceId} />
          ))}
        </div>
      )}
      {numbers.length > 0 && (
        <div className="controls-group">
          <div className="controls-group-title">Power Settings</div>
          {numbers.map(({ label, entity, unit }) => (
            <NumberControl key={entity.id} label={label} entity={entity} unit={unit} deviceId={deviceId} />
          ))}
        </div>
      )}
      {selects.length === 0 && numbers.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "8px 0" }}>
          No controllable entities found yet — waiting for data.
        </div>
      )}
    </div>
  );
}

function DiagRow({ label, value }) {
  return (
    <div className="diag-item">
      <div className="diag-item-label">{label}</div>
      <div className="diag-item-value">{value ?? "—"}</div>
    </div>
  );
}

function DiagnosticsTab({ entities }) {
  const s = (...t) => str(getEntity(entities, ...t));
  const v = (...t) => val(getEntity(entities, ...t));

  return (
    <div className="diag-grid">
      <DiagRow label="Device Name"       value={s("Device Name")} />
      <DiagRow label="Software Version"  value={s("Software Version")} />
      <DiagRow label="Firmware Version"  value={s("Firmware Version")} />
      <DiagRow label="BMS Version"       value={s("BMS Version")} />
      <DiagRow label="ESP IP"            value={s("ESP IP")} />
      <DiagRow label="ESP SSID"          value={s("ESP SSID")} />
      <DiagRow label="ESP Version"       value={s("ESP Version")} />
      <DiagRow label="WiFi Signal (ESP)" value={v("WiFi Signal Strength") != null ? `${v("WiFi Signal Strength")} dBm` : "—"} />
      <DiagRow label="WiFi Signal (BAT)" value={s("Battery Wifi Signal Strength") ? `${v("Battery Wifi Signal Strength")} dBm` : "—"} />
      <DiagRow label="WiFi Status"       value={s("Wifi status")} />
      <DiagRow label="BT Status"         value={s("BT status")} />
      <DiagRow label="Cloud Status"      value={s("Cloud status")} />
      <DiagRow label="Power Restriction" value={s("Power restriction")} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit Device Modal
// ---------------------------------------------------------------------------
function EditDeviceModal({ device, onClose, onSaved }) {
  const [name, setName]   = useState(device.name);
  const [ip, setIp]       = useState(device.ip);
  const [port, setPort]   = useState(device.port);
  const [saving, setSaving] = useState(false);
  const [error, setError]   = useState(null);

  const handleSave = async () => {
    if (!name.trim() || !ip.trim()) { setError("Name and IP are required."); return; }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/devices/${device.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), ip: ip.trim(), port: Number(port) }),
      });
      if (!res.ok) { setError("Save failed."); return; }
      const updated = await res.json();
      onSaved(updated);
    } catch {
      setError("Network error.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <span className="modal-title">Edit Device</span>
          <button className="btn btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {error && <div className="form-error">{error}</div>}
          <div className="form-group">
            <label className="form-label">Name</label>
            <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">IP Address</label>
            <input className="form-input" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="192.168.1.x" />
          </div>
          <div className="form-group">
            <label className="form-label">Port</label>
            <input className="form-input" type="number" value={port} onChange={(e) => setPort(e.target.value)} min={1} max={65535} />
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main DeviceCard
// ---------------------------------------------------------------------------

const TABS = [
  { id: "overview",     label: "Overview" },
  { id: "energy",       label: "Energy" },
  { id: "temperature",  label: "Temperature" },
  { id: "alarms",       label: "Alarms" },
  { id: "controls",     label: "Controls" },
  { id: "diagnostics",  label: "Diagnostics" },
];

export default function DeviceCard({ device, onDelete, onEdit, onPowerUpdate }) {
  const [entities, setEntities]         = useState({});
  const [online, setOnline]             = useState(false);
  const [loading, setLoading]           = useState(true);
  const [lastUpdate, setLastUpdate]     = useState(null);
  const [tab, setTab]                   = useState("overview");
  const [showDelete, setShowDelete]     = useState(false);
  const [showEdit, setShowEdit]         = useState(false);
  const [streamError, setStreamError]   = useState(null);
  // "live" dot pulse ref
  const liveDotRef = useRef(null);

  // ── SSE connection ────────────────────────────────────────────────────────
  useEffect(() => {
    setEntities({});
    setOnline(false);
    setLoading(true);
    setStreamError(null);

    console.log(`[DeviceCard] Connecting SSE: /api/devices/${device.id}/stream  (${device.ip}:${device.port})`);
    const es = new EventSource(`/api/devices/${device.id}/stream`);

    es.addEventListener("state", (e) => {
      try {
        const entity = JSON.parse(e.data);
        if (entity.id) {
          // Merge into existing entity so static fields (options, min/max/step)
          // received in the initial state burst are not lost on subsequent updates.
          setEntities((prev) => ({
            ...prev,
            [entity.id]: { ...(prev[entity.id] ?? {}), ...entity },
          }));
          setOnline(true);
          setLoading(false);
          setStreamError(null);
          setLastUpdate(Date.now());
          // Pulse the live dot
          if (liveDotRef.current) {
            liveDotRef.current.classList.remove("pulse");
            void liveDotRef.current.offsetWidth; // reflow
            liveDotRef.current.classList.add("pulse");
          }
        }
      } catch (err) {
        console.warn(`[DeviceCard] SSE state parse error (${device.name}):`, err, e.data);
      }
    });

    es.addEventListener("ping", () => {
      setOnline(true);
      setLoading(false);
    });

    // Custom "error" event sent by our Flask backend (has e.data)
    es.addEventListener("error", (e) => {
      if (e.data) {
        try {
          const { error } = JSON.parse(e.data);
          console.error(`[DeviceCard] SSE backend error (${device.name}):`, error);
          setStreamError(error);
        } catch {
          console.error(`[DeviceCard] SSE error event (${device.name}):`, e.data);
          setStreamError(e.data);
        }
      }
      setOnline(false);
      setLoading(false);
    });

    // Native EventSource connection error (no e.data)
    es.onerror = (e) => {
      console.error(`[DeviceCard] SSE connection error (${device.name}):`, e);
      setOnline(false);
      setLoading(false);
    };

    return () => { console.log(`[DeviceCard] Closing SSE (${device.name})`); es.close(); };
  }, [device.id, device.ip, device.port, device.name]);

  // ── Derived values ────────────────────────────────────────────────────────
  const soc           = val(getEntity(entities, "State Of Charge"));
  const remaining     = val(getEntity(entities, "Remaining Capacity"));
  const total         = val(getEntity(entities, "Battery Total Energy"));
  const inverterState = str(getEntity(entities, "Inverter State"));

  // Raw sensor values
  const rawAcPower  = val(getEntity(entities, "AC Power"));
  const rawBatPower = val(getEntity(entities, "Battery Power"));
  const acVoltage   = val(getEntity(entities, "AC Voltage"));

  // Per-phase voltages (optional – may not exist on all devices)
  const l1V = val(getEntity(entities, "L1", "Voltage")) ?? val(getEntity(entities, "Phase 1", "Voltage"));
  const l2V = val(getEntity(entities, "L2", "Voltage")) ?? val(getEntity(entities, "Phase 2", "Voltage"));
  const l3V = val(getEntity(entities, "L3", "Voltage")) ?? val(getEntity(entities, "Phase 3", "Voltage"));
  const phaseVoltages = (l1V != null || l2V != null || l3V != null)
    ? { L1: l1V, L2: l2V, L3: l3V }
    : null;

  // Apply user-configured sign inversion
  const { invertAcFlow, invertBatFlow } = getFlowSettings();
  const acPower  = rawAcPower  != null ? rawAcPower  * (invertAcFlow  ? -1 : 1) : null;
  const batPower = rawBatPower != null ? rawBatPower * (invertBatFlow ? -1 : 1) : null;

  // Report power values to parent for aggregated home view
  useEffect(() => {
    onPowerUpdate?.(device.id, { acPower, batPower, acVoltage, phaseVoltages, soc, l1V, l2V, l3V });
  }, [acPower, batPower, acVoltage, phaseVoltages, soc, l1V, l2V, l3V, device.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const alarmCount = Object.values(entities).filter(
    (e) => e.id && e.id.startsWith("binary_sensor/") && e.state === "ON"
  ).length;

  const tsLabel = lastUpdate
    ? new Date(lastUpdate).toLocaleTimeString()
    : null;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="device-card">
      {/* ── Header ── */}
      <div className="device-card-header">
        <div className="device-card-header-left">
          <div className={`status-dot ${online ? "online" : "offline"}`} title={online ? "Online" : "Offline"} />
          <div>
            <div className="device-card-name">{device.name}</div>
            <div className="device-card-ip">{device.ip}:{device.port}</div>
          </div>
          {inverterState && (
            <span className={`badge ${inverterBadgeClass(inverterState)}`}>{inverterState}</span>
          )}
        </div>

        <div className="device-card-header-right">
          {/* Live indicator */}
          {online && (
            <span ref={liveDotRef} className="live-indicator" title="Live SSE stream">
              LIVE
            </span>
          )}
          {/* Alarm badge */}
          {alarmCount > 0 && (
            <span className="badge badge-red" style={{ cursor: "pointer" }} onClick={() => setTab("alarms")} title="Active alarms">
              ⚠ {alarmCount}
            </span>
          )}
          {/* Edit button */}
          <button className="btn btn-icon" onClick={() => setShowEdit(true)} title="Edit device">
            ✏
          </button>
          {/* Delete button */}
          <button
            className="btn btn-icon"
            style={{ color: "var(--red)" }}
            onClick={() => setShowDelete(true)}
            title="Remove device"
          >
            ✕
          </button>
        </div>
      </div>

      {/* ── Offline banner ── */}
      {!loading && !online && (
        <div className="offline-banner">
          <span>⚠</span>
          <span>
            Device offline{tsLabel ? ` — last seen ${tsLabel}` : ""}
            {streamError && (
              <span style={{ display: "block", fontSize: 11, marginTop: 2, opacity: 0.8, fontFamily: "monospace" }}>
                {streamError}
              </span>
            )}
          </span>
        </div>
      )}

      {/* ── Hero ── */}
      {loading ? (
        <div className="loading-overlay">
          <div className="loading-spinner" />
          <span>Connecting…</span>
        </div>
      ) : (
        <>
          <div className="device-hero">
            <BatteryGauge soc={soc} remaining={remaining} total={total} />
            <PowerFlow
              acPower={acPower}
              batteryPower={batPower}
              acVoltage={acVoltage}
              phaseVoltages={phaseVoltages}
            />
          </div>

          {/* ── Tab bar ── */}
          <div className="tab-bar" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`tab-btn ${tab === t.id ? "active" : ""}`}
                onClick={() => setTab(t.id)}
                role="tab"
                aria-selected={tab === t.id}
              >
                {t.label}
                {t.id === "alarms" && alarmCount > 0 && (
                  <span style={{ display:"inline-flex",alignItems:"center",justifyContent:"center",
                    width:16,height:16,background:"var(--red)",color:"#fff",borderRadius:"50%",
                    fontSize:9,fontWeight:700,marginLeft:4 }}>
                    {alarmCount}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* ── Tab content ── */}
          <div className="tab-content" role="tabpanel">
            {tab === "overview"    && <OverviewTab    entities={entities} />}
            {tab === "energy"      && <EnergyTab      entities={entities} />}
            {tab === "temperature" && <TemperatureTab entities={entities} />}
            {tab === "alarms"      && <AlarmsTab      entities={entities} />}
            {tab === "controls"    && <ControlsTab    entities={entities} deviceId={device.id} />}
            {tab === "diagnostics" && <DiagnosticsTab entities={entities} />}
          </div>
        </>
      )}

      {/* ── Footer ── */}
      {!loading && (
        <div className="card-footer">
          <span>{tsLabel ? `Last update: ${tsLabel}` : "Waiting for data…"}</span>
          <span style={{ color: "var(--green)", fontSize: 11, fontWeight: 600 }}>
            {online ? "● Streaming live" : "○ Reconnecting…"}
          </span>
        </div>
      )}

      {/* ── Edit modal ── */}
      {showEdit && (
        <EditDeviceModal
          device={device}
          onClose={() => setShowEdit(false)}
          onSaved={(updated) => { onEdit?.(updated); setShowEdit(false); }}
        />
      )}

      {/* ── Delete confirm ── */}
      {showDelete && (
        <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && setShowDelete(false)}>
          <div className="modal" style={{ maxWidth: 340 }}>
            <div className="modal-header">
              <span className="modal-title">Remove Device</span>
              <button className="btn btn-icon" onClick={() => setShowDelete(false)}>✕</button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: 14, color: "var(--text-muted)" }}>
                Remove <strong style={{ color: "var(--text)" }}>{device.name}</strong>? This cannot be undone.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn btn-ghost" onClick={() => setShowDelete(false)}>Cancel</button>
              <button className="btn btn-danger" onClick={() => { setShowDelete(false); onDelete(device.id); }}>
                Remove
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
