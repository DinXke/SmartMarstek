/**
 * ForecastPage – Zonneopbrengst voorspelling via forecast.solar
 * Toont vandaag en morgen als staafdiagram (15-minuten intervallen).
 */
import { useState, useEffect, useCallback } from "react";

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtHour(ts) {
  // ts = "2024-04-01 08:15:00"
  return ts.slice(11, 16);
}

function fmtKwh(wh) {
  if (wh == null) return "—";
  if (wh >= 1000) return `${(wh / 1000).toFixed(2)} kWh`;
  return `${Math.round(wh)} Wh`;
}

function fmtW(w) {
  if (w == null) return "—";
  if (w >= 1000) return `${(w / 1000).toFixed(2)} kW`;
  return `${Math.round(w)} W`;
}

function today()    { return new Date().toISOString().slice(0, 10); }
function tomorrow() {
  const d = new Date(); d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

// Filter watts/wh_period to a specific date (YYYY-MM-DD)
function filterDay(obj, date) {
  return Object.entries(obj)
    .filter(([k]) => k.startsWith(date))
    .sort(([a], [b]) => a.localeCompare(b));
}

// ── Bar chart ─────────────────────────────────────────────────────────────────

function BarChart({ slots, color, unit, maxVal }) {
  const now    = new Date();
  const nowStr = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
  const max    = maxVal || Math.max(1, ...slots.map(([, v]) => v));

  if (!slots.length) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "24px 0", textAlign: "center" }}>
        Geen data beschikbaar voor deze dag.
      </div>
    );
  }

  return (
    <div className="forecast-chart">
      {slots.map(([ts, val], i) => {
        const h    = fmtHour(ts);
        const pct  = Math.round((val / max) * 100);
        const isPast = h <= nowStr;
        const showLabel = i % 4 === 0 || i === slots.length - 1;
        return (
          <div key={ts} className="forecast-bar-col" title={`${h}  ${unit === "W" ? fmtW(val) : fmtKwh(val)}`}>
            <div className="forecast-bar-track">
              <div
                className="forecast-bar-fill"
                style={{
                  height: `${pct}%`,
                  background: isPast
                    ? `rgba(${color},0.35)`
                    : `rgba(${color},0.85)`,
                  boxShadow: isPast ? "none" : `0 0 6px rgba(${color},0.7)`,
                }}
              />
            </div>
            {showLabel && (
              <div className="forecast-bar-label">{h}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Day panel ─────────────────────────────────────────────────────────────────

function DayPanel({ title, date, watts, whPeriod, whDay, isToday }) {
  const wSlots  = filterDay(watts,    date);
  const wpSlots = filterDay(whPeriod, date);
  const totalWh = whDay[date] ?? wpSlots.reduce((s, [, v]) => s + v, 0);

  // Find peak
  const peak    = wSlots.length ? wSlots.reduce((m, [, v]) => Math.max(m, v), 0) : null;
  const peakTs  = peak != null ? wSlots.find(([, v]) => v === peak)?.[0] : null;

  // Up to now (today only)
  let producedWh = null;
  if (isToday) {
    const now = new Date();
    const nowStr = now.toISOString().replace("T", " ").slice(0, 19);
    producedWh = wpSlots
      .filter(([k]) => k <= nowStr)
      .reduce((s, [, v]) => s + v, 0);
  }

  return (
    <div className="forecast-day-panel">
      <div className="forecast-day-header">
        <span className="forecast-day-title">{title}</span>
        <div className="forecast-day-stats">
          <span className="forecast-stat">
            <span className="forecast-stat-label">Verwacht totaal</span>
            <span className="forecast-stat-value" style={{ color: "#ffd600" }}>{fmtKwh(totalWh)}</span>
          </span>
          {peak != null && (
            <span className="forecast-stat">
              <span className="forecast-stat-label">Piek</span>
              <span className="forecast-stat-value" style={{ color: "#4ade80" }}>
                {fmtW(peak)}
                {peakTs && <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> om {fmtHour(peakTs)}</span>}
              </span>
            </span>
          )}
          {isToday && producedWh != null && (
            <span className="forecast-stat">
              <span className="forecast-stat-label">Geproduceerd</span>
              <span className="forecast-stat-value" style={{ color: "#38bdf8" }}>{fmtKwh(producedWh)}</span>
            </span>
          )}
        </div>
      </div>

      {/* Power (W) chart */}
      <div className="forecast-chart-label">Vermogen (W)</div>
      <BarChart slots={wSlots}  color="255,214,0"  unit="W"   />

      {/* Energy per period (Wh) chart */}
      <div className="forecast-chart-label" style={{ marginTop: 12 }}>Energie per kwartier (Wh)</div>
      <BarChart slots={wpSlots} color="74,222,128" unit="Wh"  />
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ForecastPage() {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [lastFetch, setLastFetch] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch("/api/forecast/estimate");
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.error || `HTTP ${r.status}`);
      }
      setData(await r.json());
      setLastFetch(new Date());
    } catch (e) { setError(e.message); }
    finally     { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const todayStr    = today();
  const tomorrowStr = tomorrow();

  return (
    <div className="forecast-page">
      <div className="forecast-header">
        <div>
          <div className="forecast-title">☀️ Zonneopbrengst voorspelling</div>
          {lastFetch && (
            <div className="forecast-subtitle">
              Bijgewerkt: {lastFetch.toLocaleTimeString("nl-BE")}
              <span style={{ color: "var(--text-dim)", marginLeft: 8 }}>· cache 15 min</span>
            </div>
          )}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={load} disabled={loading}>
          {loading ? "Laden…" : "↺ Vernieuwen"}
        </button>
      </div>

      {loading && !data && (
        <div className="loading-overlay" style={{ position: "relative", height: 120 }}>
          <div className="loading-spinner" />
          <span>Voorspelling ophalen…</span>
        </div>
      )}

      {error && (
        <div className="forecast-error">
          <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠ Kon voorspelling niet laden</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{error}</div>
          {error.includes("niet ingesteld") && (
            <div style={{ marginTop: 8, fontSize: 12 }}>
              Stel de locatie in via <strong>Instellingen → Forecast.Solar</strong>.
            </div>
          )}
        </div>
      )}

      {data && !error && (
        <>
          {data.errors?.length > 0 && (
            <div className="forecast-error" style={{ marginBottom: 16 }}>
              {data.errors.map((e, i) => <div key={i}>⚠ {e}</div>)}
            </div>
          )}

          <DayPanel
            title="Vandaag"
            date={todayStr}
            watts={data.watts}
            whPeriod={data.watt_hours_period}
            whDay={data.watt_hours_day}
            isToday={true}
          />
          <DayPanel
            title="Morgen"
            date={tomorrowStr}
            watts={data.watts}
            whPeriod={data.watt_hours_period}
            whDay={data.watt_hours_day}
            isToday={false}
          />
        </>
      )}
    </div>
  );
}
