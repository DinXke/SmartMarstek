import { useState, useEffect, useRef } from "react";

// Color matching EnergyMap solar palette
const SOLAR_COLOR = "#ffd600";
const MUTED       = "var(--text-muted)";

function Stat({ label, value, unit, color }) {
  return (
    <div style={{ textAlign: "center", minWidth: 80 }}>
      <div style={{ fontSize: 11, color: MUTED, marginBottom: 2, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color ?? "var(--text)", lineHeight: 1 }}>
        {value ?? "—"}
        {unit && value != null && (
          <span style={{ fontSize: 12, fontWeight: 400, color: MUTED, marginLeft: 3 }}>{unit}</span>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status, online }) {
  if (!online) return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 12,
      background: "rgba(255,80,80,.15)", color: "#ff5050", fontSize: 11, fontWeight: 600,
    }}>Offline</span>
  );
  if (!status) return null;
  const isOk    = status.toLowerCase().includes("netinvoer") || status.toLowerCase().includes("mpp");
  const isWarn  = status.toLowerCase().includes("beperkt") || status.toLowerCase().includes("wacht");
  const isError = status.toLowerCase().includes("fout");
  const bg = isError ? "rgba(255,80,80,.15)" : isWarn ? "rgba(255,160,0,.15)" : "rgba(0,230,100,.15)";
  const fg = isError ? "#ff5050" : isWarn ? "#ffa000" : "#00e676";
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 12,
      background: bg, color: fg, fontSize: 11, fontWeight: 600,
    }}>{status}</span>
  );
}

function fmtW(w) {
  if (w == null) return null;
  return Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(2)}` : `${Math.round(w)}`;
}
function wUnit(w) {
  if (w == null) return "";
  return Math.abs(w) >= 1000 ? "kW" : "W";
}

export default function SmaInverterPanel({ refreshTick }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [open,    setOpen]    = useState(true);
  const intervalRef = useRef(null);

  function fetch_data() {
    fetch("api/sma/live")
      .then((r) => r.json())
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }

  useEffect(() => {
    fetch_data();
    intervalRef.current = setInterval(fetch_data, 10_000);
    return () => clearInterval(intervalRef.current);
  }, []);

  // Also refresh when parent dashboard refreshes
  useEffect(() => { if (refreshTick) fetch_data(); }, [refreshTick]);

  // Never fully hide — always show so user can discover the feature

  const online  = data?.online ?? false;
  const status  = null; // status removed from live data — shown via InfluxDB
  const pac     = data?.pac_w;
  const eDay    = data?.e_day_wh;
  const eTotal  = data?.e_total_wh;
  const gridV   = data?.grid_v;
  const freqHz  = data?.freq_hz;
  const tempC   = data?.temp_c;
  const ageS    = data?.age_s;

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 12,
      background: "var(--card)", overflow: "hidden",
      margin: "0 0 16px",
    }}>
      {/* Header */}
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", background: "none", border: "none", cursor: "pointer",
          color: "var(--text)", gap: 8,
        }}
        aria-expanded={open}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 18 }}>☀️</span>
          <span style={{ fontWeight: 600, fontSize: 14 }}>SMA Sunny Boy</span>
          <StatusBadge status={status} online={online} />
          {online && pac != null && (
            <span style={{ fontSize: 13, color: SOLAR_COLOR, fontWeight: 700 }}>
              {fmtW(pac)} {wUnit(pac)}
            </span>
          )}
        </div>
        <span style={{ fontSize: 12, color: MUTED, userSelect: "none" }}>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={{ padding: "0 16px 16px" }}>
          {loading && (
            <div style={{ textAlign: "center", color: MUTED, padding: 16, fontSize: 13 }}>
              Laden…
            </div>
          )}

          {!loading && !online && (
            <div style={{
              fontSize: 13, padding: "8px 12px", borderRadius: 8,
              background: "rgba(255,214,0,.06)", border: "1px dashed rgba(255,214,0,.25)",
              color: MUTED,
            }}>
              SMA Modbus reader niet actief.
              Ga naar <strong>Instellingen → Apparaten &amp; strategie → 📡 SMA Reader</strong> om het IP-adres in te stellen en de reader in te schakelen.
            </div>
          )}

          {!loading && online && (
            <>
              {/* Main power stats */}
              <div style={{
                display: "flex", flexWrap: "wrap", gap: 20,
                justifyContent: "space-around", marginBottom: 20,
                padding: "12px 0", borderBottom: "1px solid var(--border)",
              }}>
                <Stat label="AC-vermogen" value={fmtW(pac)} unit={wUnit(pac)} color={pac > 50 ? SOLAR_COLOR : MUTED} />
                <Stat label="Vandaag" value={eDay != null ? (eDay / 1000).toFixed(2) : null} unit="kWh" color={SOLAR_COLOR} />
                <Stat label="Totaal" value={eTotal != null ? (eTotal / 1000 / 1000).toFixed(0) : null} unit="MWh" />
              </div>

              {/* Secondary stats grid */}
              <div style={{
                display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px 16px",
              }}>
                <Stat label="Netspanning" value={gridV} unit="V" />
                <Stat label="Frequentie"  value={freqHz != null ? freqHz.toFixed(2) : null} unit="Hz" />
                <Stat label="Temperatuur" value={tempC} unit="°C" />
                {ageS != null && (
                  <div style={{ textAlign: "center", minWidth: 80 }}>
                    <div style={{ fontSize: 11, color: MUTED, marginBottom: 2, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      Update
                    </div>
                    <div style={{ fontSize: 12, color: ageS < 20 ? "var(--success)" : "var(--warning)", fontWeight: 600 }}>
                      {ageS} s
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
