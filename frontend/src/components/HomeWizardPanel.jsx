import { useState, useEffect, useCallback } from "react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtValue(value, unit) {
  if (value == null) return "—";
  if (unit === "W") {
    const abs = Math.abs(value);
    if (abs >= 1000) return `${(value / 1000).toFixed(2)} kW`;
    return `${Math.round(value)} W`;
  }
  if (unit === "kWh") return `${value.toFixed(3)} kWh`;
  if (unit === "m³")  return `${value.toFixed(3)} m³`;
  if (unit === "V")   return `${value.toFixed(1)} V`;
  if (unit === "A")   return `${value.toFixed(2)} A`;
  if (unit === "Hz")  return `${value.toFixed(2)} Hz`;
  if (unit === "VA" || unit === "VAr") return `${Math.round(value)} ${unit}`;
  if (unit === "L/min") return `${value.toFixed(1)} L/min`;
  if (unit === "%")   return `${value.toFixed(1)} %`;
  if (unit === "")    return `${value}`;
  return `${value} ${unit}`;
}

/** For power sensors: positive = importing from grid (warm), negative = exporting (green) */
function powerColor(value) {
  if (value > 50)   return "var(--red)";
  if (value < -50)  return "var(--green)";
  return "var(--text-muted)";
}

// ---------------------------------------------------------------------------
// Sensor card
// ---------------------------------------------------------------------------

function SensorCard({ label, value, unit, power }) {
  const color = power ? powerColor(value) : "var(--text)";
  const formatted = fmtValue(value, unit);

  return (
    <div className="hw-sensor-card">
      <div className="hw-sensor-label">{label}</div>
      <div className="hw-sensor-value" style={{ color }}>{formatted}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Device block
// ---------------------------------------------------------------------------

function DeviceBlock({ device }) {
  const sensors = Object.values(device.sensors);

  // Group sensors
  const groups = {};
  for (const s of sensors) {
    if (!groups[s.group]) groups[s.group] = [];
    groups[s.group].push(s);
  }

  return (
    <div className="hw-device-block">
      <div className="hw-device-header">
        <div className="hw-device-name">{device.name}</div>
        <div className="hw-device-meta">
          {device.product_type && <span className="hw-badge">{device.product_type}</span>}
          {!device.reachable && <span className="hw-badge hw-badge-err">offline</span>}
          {device.error && <span className="hw-device-error">{device.error}</span>}
        </div>
      </div>
      {sensors.length > 0 && (
        <div className="hw-sensors-grid">
          {sensors.map((s) => (
            <SensorCard key={s.label + s.unit} {...s} />
          ))}
        </div>
      )}
      {sensors.length === 0 && device.reachable && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 0" }}>
          Geen sensoren geselecteerd — stel in via Instellingen.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function HomeWizardPanel() {
  const [data,     setData]     = useState(null);
  const [lastPoll, setLastPoll] = useState(null);

  const poll = useCallback(async () => {
    try {
      const r = await fetch("/api/homewizard/data");
      if (!r.ok) return;
      setData(await r.json());
      setLastPoll(new Date());
    } catch { /* silently skip */ }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 10000);
    return () => clearInterval(id);
  }, [poll]);

  // Don't render anything if no devices are configured
  if (!data?.devices?.length) return null;

  const timeStr = lastPoll
    ? lastPoll.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "";

  return (
    <div className="hw-panel">
      <div className="hw-panel-header">
        <span className="hw-panel-title">🏠 HomeWizard</span>
        {timeStr && <span className="hw-panel-ts">bijgewerkt {timeStr}</span>}
      </div>
      <div className="hw-devices-row">
        {data.devices.map((dev) => (
          <DeviceBlock key={dev.id} device={dev} />
        ))}
      </div>
    </div>
  );
}
