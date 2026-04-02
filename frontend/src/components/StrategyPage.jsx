/**
 * StrategyPage – Battery charging strategy visualisation
 *
 * Timeline chart (today + tomorrow, or single historical day):
 *   Row 1: Energy price (€/kWh) – colour-coded bar
 *   Row 2: Solar forecast (Wh) – yellow bars
 *   Row 3: Expected consumption (Wh) – blue bars
 *   Row 4: Battery action band – colour coded per action
 *   Row 5: Predicted SOC line
 */
import { useState, useEffect, useCallback } from "react";
import { loadFlowCfg, saveFlowCfg, FLOW_CFG_KEY } from "./FlowSourcesSettings.jsx";

// ── Action colours ─────────────────────────────────────────────────────────
const ACTION_COLOR = {
  solar_charge: { bg: "rgba(253,224,71,0.25)",   border: "#fbbf24", label: "Zonneladen",    icon: "☀️" },
  grid_charge:  { bg: "rgba(74,222,128,0.22)",   border: "#22c55e", label: "Netwerk laden", icon: "⚡" },
  save:         { bg: "rgba(251,191,36,0.18)",   border: "#f59e0b", label: "Sparen",        icon: "🔒" },
  discharge:    { bg: "rgba(248,113,113,0.22)",  border: "#ef4444", label: "Ontladen",      icon: "🔋" },
  neutral:      { bg: "rgba(100,116,139,0.10)",  border: "#475569", label: "Neutraal",      icon: "·"  },
};

// ── Date helpers ──────────────────────────────────────────────────────────
function todayStr()    { return new Date().toISOString().slice(0, 10); }
function tomorrowStr() {
  const d = new Date(); d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}
function prevDay(d) {
  const dt = new Date(d + "T12:00:00"); dt.setDate(dt.getDate() - 1);
  return dt.toISOString().slice(0, 10);
}
function nextDay(d) {
  const dt = new Date(d + "T12:00:00"); dt.setDate(dt.getDate() + 1);
  return dt.toISOString().slice(0, 10);
}
function fmtDate(d) {
  return new Date(d + "T12:00:00").toLocaleDateString("nl-BE", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}

function fmtPrice(p) {
  if (p == null) return "—";
  return `${Math.round(p * 100)} ct`;
}
function fmtWh(w) {
  if (w == null || w === 0) return "";
  if (w >= 1000) return `${(w / 1000).toFixed(1)} kWh`;
  return `${Math.round(w)} Wh`;
}
function fmtHour(ts) {
  return ts ? ts.slice(11, 16) : "";
}

// ── Sync flow config to backend so influx_writer can read it ─────────────
function syncFlowCfgToBackend() {
  const cfg = loadFlowCfg();
  fetch("api/flow/cfg", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  }).catch(() => {});
}

// ── Sub-components ────────────────────────────────────────────────────────

function LegendItem({ color, label }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
      <div style={{ width: 12, height: 12, borderRadius: 3,
        background: color.bg, border: `1.5px solid ${color.border}` }} />
      <span>{color.icon} {label}</span>
    </div>
  );
}

function HourBar({ slot, maxPrice, maxSolar, maxCons, isNow }) {
  const ac     = ACTION_COLOR[slot.action] || ACTION_COLOR.neutral;
  const pPct   = maxPrice > 0 && slot.price_eur_kwh != null ? Math.max(2, (slot.price_eur_kwh / maxPrice) * 100) : 0;
  const sPct   = maxSolar > 0 ? Math.max(0, (slot.solar_wh / maxSolar) * 100) : 0;
  const cPct   = maxCons  > 0 ? Math.max(2, (slot.consumption_wh / maxCons) * 100) : 0;
  const isPast = slot.is_past;

  const priceColor = slot.price_eur_kwh == null ? "#475569"
    : slot.price_eur_kwh < 0.05 ? "#22c55e"
    : slot.price_eur_kwh < 0.10 ? "#84cc16"
    : slot.price_eur_kwh < 0.15 ? "#eab308"
    : slot.price_eur_kwh < 0.20 ? "#f97316"
    : "#ef4444";

  return (
    <div className={`strat-hour-col ${isNow ? "strat-now" : ""}`}
      style={{ opacity: isPast ? 0.35 : 1 }}
      title={`${fmtHour(slot.time)} · ${fmtPrice(slot.price_eur_kwh)} · ${slot.reason || slot.action}`}>

      {/* Price bar */}
      <div className="strat-bar-track strat-price-track">
        <div className="strat-bar-fill" style={{ height: `${pPct}%`, background: priceColor, opacity: 0.85 }} />
      </div>

      {/* Solar bar */}
      <div className="strat-bar-track strat-solar-track">
        <div className="strat-bar-fill" style={{ height: `${sPct}%`, background: "#fbbf24", opacity: 0.8 }} />
      </div>

      {/* Consumption bar */}
      <div className="strat-bar-track strat-cons-track">
        <div className="strat-bar-fill" style={{ height: `${cPct}%`, background: "#60a5fa", opacity: 0.7 }} />
      </div>

      {/* Action band */}
      <div className="strat-action-band"
        style={{ background: ac.bg, borderTop: `2px solid ${ac.border}` }}>
        <span style={{ fontSize: 9 }}>{ac.icon}</span>
      </div>

      {/* SOC arc / number */}
      <div className="strat-soc-val"
        style={{ color: slot.soc_end < 20 ? "#ef4444" : slot.soc_end < 50 ? "#f59e0b" : "#4ade80" }}>
        {Math.round(slot.soc_end)}
      </div>

      {/* Hour label */}
      <div className="strat-hour-label">{slot.hour === 0 || slot.hour % 3 === 0 ? `${slot.hour}h` : ""}</div>
    </div>
  );
}

function DayChart({ title, slots, isToday }) {
  if (!slots || !slots.length) return (
    <div className="strat-day-panel">
      <div className="strat-day-title">{title}</div>
      <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "16px 0" }}>
        Geen data beschikbaar. Stel energieprijzen en/of zonneopbrengst in.
      </div>
    </div>
  );

  const maxPrice = Math.max(0.01, ...slots.map((s) => s.price_eur_kwh || 0));
  const maxSolar = Math.max(1, ...slots.map((s) => s.solar_wh || 0));
  const maxCons  = Math.max(1, ...slots.map((s) => s.consumption_wh || 0));
  const nowHour  = new Date().getHours();

  const totalSolar   = slots.reduce((a, s) => a + (s.solar_wh || 0), 0);
  const totalCons    = slots.reduce((a, s) => a + (s.consumption_wh || 0), 0);
  const gridSlots    = slots.filter((s) => s.action === "grid_charge");
  const disSlots     = slots.filter((s) => s.action === "discharge");
  const saveSlots    = slots.filter((s) => s.action === "save");
  const cheapestGrid = gridSlots.length ? Math.min(...gridSlots.map((s) => s.price_eur_kwh || 999)) : null;
  const peakPrice    = Math.max(0, ...slots.map((s) => s.price_eur_kwh || 0));

  return (
    <div className="strat-day-panel">
      <div className="strat-day-header">
        <span className="strat-day-title">{title}</span>
        <div className="strat-day-stats">
          {totalSolar > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Zon</span>
              <span className="strat-stat-val" style={{ color: "#fbbf24" }}>{fmtWh(totalSolar)}</span>
            </span>
          )}
          <span className="strat-stat">
            <span className="strat-stat-lbl">Verbruik</span>
            <span className="strat-stat-val" style={{ color: "#60a5fa" }}>{fmtWh(totalCons)}</span>
          </span>
          {gridSlots.length > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Laadmomenten</span>
              <span className="strat-stat-val" style={{ color: "#22c55e" }}>{gridSlots.length}u · goedkoopste {fmtPrice(cheapestGrid)}</span>
            </span>
          )}
          {disSlots.length > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Ontlaadmomenten</span>
              <span className="strat-stat-val" style={{ color: "#ef4444" }}>{disSlots.length}u · piek {fmtPrice(peakPrice)}</span>
            </span>
          )}
          {saveSlots.length > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Sparen</span>
              <span className="strat-stat-val" style={{ color: "#f59e0b" }}>{saveSlots.length}u</span>
            </span>
          )}
        </div>
      </div>

      <div className="strat-chart-legend">
        <div className="strat-legend-row" style={{ color: "#94a3b8", fontSize: 10, marginBottom: 4 }}>
          <span>▲ Prijs</span><span>▲ Zon</span><span>▲ Verbruik</span><span>Actie</span><span>SOC%</span>
        </div>
      </div>

      <div className="strat-chart">
        {slots.map((slot) => (
          <HourBar key={slot.time} slot={slot}
            maxPrice={maxPrice} maxSolar={maxSolar} maxCons={maxCons}
            isNow={isToday && slot.hour === nowHour && !slot.is_past}
          />
        ))}
      </div>

      {slots.some((s) => s.action !== "neutral") && (
        <div className="strat-detail-list">
          {slots.filter((s) => s.action !== "neutral").map((slot) => {
            const ac = ACTION_COLOR[slot.action] || ACTION_COLOR.neutral;
            return (
              <div key={slot.time} className="strat-detail-row"
                style={{ borderLeft: `3px solid ${ac.border}` }}>
                <span className="strat-detail-time">{fmtHour(slot.time)}</span>
                <span className="strat-detail-icon">{ac.icon}</span>
                <span className="strat-detail-action" style={{ color: ac.border }}>{ac.label}</span>
                <span className="strat-detail-reason">{slot.reason}</span>
                <span className="strat-detail-soc">
                  {Math.round(slot.soc_start)}% → {Math.round(slot.soc_end)}%
                </span>
                {slot.price_eur_kwh != null && (
                  <span className="strat-detail-price">{fmtPrice(slot.price_eur_kwh)}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Actuals chart (what really happened, from InfluxDB) ──────────────────

function ActualsChart({ actuals }) {
  if (!actuals || !Object.keys(actuals).length) return (
    <div className="strat-day-panel" style={{ color: "var(--text-muted)", fontSize: 13 }}>
      📊 Geen InfluxDB data voor deze dag.
    </div>
  );

  const hours = Array.from({ length: 24 }, (_, h) => ({
    hour:   h,
    solar:  actuals[h]?.solar_w ?? 0,
    house:  actuals[h]?.house_w ?? 0,
    net:    actuals[h]?.net_w   ?? 0,
    soc:    actuals[h]?.bat_soc ?? null,
  }));

  const maxSolar = Math.max(1, ...hours.map((h) => h.solar));
  const maxHouse = Math.max(1, ...hours.map((h) => h.house));
  const maxNet   = Math.max(1, ...hours.map((h) => Math.abs(h.net)));
  const hasSoc   = hours.some((h) => h.soc !== null);

  const totalSolar = hours.reduce((a, h) => a + h.solar, 0);
  const totalHouse = hours.reduce((a, h) => a + h.house, 0);
  const totalImport = hours.reduce((a, h) => a + Math.max(0, h.net), 0);
  const totalExport = hours.reduce((a, h) => a + Math.max(0, -h.net), 0);

  return (
    <div className="strat-day-panel">
      <div className="strat-day-header">
        <span className="strat-day-title">📊 Wat er echt gebeurde</span>
        <div className="strat-day-stats">
          {totalSolar > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Zonopbrengst</span>
              <span className="strat-stat-val" style={{ color: "#fbbf24" }}>{fmtWh(totalSolar)}</span>
            </span>
          )}
          <span className="strat-stat">
            <span className="strat-stat-lbl">Verbruik</span>
            <span className="strat-stat-val" style={{ color: "#60a5fa" }}>{fmtWh(totalHouse)}</span>
          </span>
          {totalImport > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Afname net</span>
              <span className="strat-stat-val" style={{ color: "#f97316" }}>{fmtWh(totalImport)}</span>
            </span>
          )}
          {totalExport > 0 && (
            <span className="strat-stat">
              <span className="strat-stat-lbl">Injectie net</span>
              <span className="strat-stat-val" style={{ color: "#22c55e" }}>{fmtWh(totalExport)}</span>
            </span>
          )}
        </div>
      </div>

      <div className="strat-chart-legend">
        <div style={{ display: "flex", gap: 14, fontSize: 10, color: "#94a3b8", marginBottom: 4, flexWrap: "wrap" }}>
          <span style={{ color: "#fbbf24" }}>▲ Zon (W gem.)</span>
          <span style={{ color: "#60a5fa" }}>▲ Verbruik (W gem.)</span>
          <span style={{ color: "#f97316" }}>▲ Afname / </span>
          <span style={{ color: "#22c55e" }}>▼ Injectie</span>
          {hasSoc && <span style={{ color: "#4ade80" }}>SOC%</span>}
        </div>
      </div>

      <div className="strat-chart">
        {hours.map(({ hour, solar, house, net, soc }) => {
          const sPct = (solar / maxSolar) * 100;
          const hPct = (house / maxHouse) * 100;
          const netPct = maxNet > 0 ? (Math.abs(net) / maxNet) * 100 : 0;
          const isImport = net > 0;
          return (
            <div key={hour} className="strat-hour-col"
              title={`${hour}:00 · zon ${Math.round(solar)}W · verbruik ${Math.round(house)}W · net ${Math.round(net)}W${soc != null ? ` · SOC ${Math.round(soc)}%` : ""}`}>
              <div className="strat-bar-track strat-price-track">
                <div className="strat-bar-fill" style={{ height: `${sPct}%`, background: "#fbbf24", opacity: 0.8 }} />
              </div>
              <div className="strat-bar-track strat-solar-track">
                <div className="strat-bar-fill" style={{ height: `${hPct}%`, background: "#60a5fa", opacity: 0.7 }} />
              </div>
              <div className="strat-bar-track strat-cons-track">
                <div className="strat-bar-fill" style={{
                  height: `${netPct}%`,
                  background: isImport ? "#f97316" : "#22c55e",
                  opacity: 0.75,
                }} />
              </div>
              <div className="strat-action-band" style={{ background: "rgba(100,116,139,0.08)", borderTop: "2px solid #334155" }}>
                <span style={{ fontSize: 8 }}>{Math.round(net)}W</span>
              </div>
              <div className="strat-soc-val"
                style={{ color: soc == null ? "#475569" : soc < 20 ? "#ef4444" : soc < 50 ? "#f59e0b" : "#4ade80" }}>
                {soc != null ? Math.round(soc) : "—"}
              </div>
              <div className="strat-hour-label">{hour === 0 || hour % 3 === 0 ? `${hour}h` : ""}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Consumption profile chart ─────────────────────────────────────────────

function ConsumptionProfile({ hours }) {
  if (!hours || !hours.length) return null;
  const max = Math.max(1, ...hours.map((h) => h.avg_wh));
  return (
    <div className="strat-day-panel">
      <div className="strat-day-title">📊 Gemiddeld verbruiksprofiel (historiek)</div>
      <div className="strat-chart" style={{ marginTop: 8 }}>
        {Array.from({ length: 24 }, (_, h) => {
          const entry = hours.find((x) => x.hour === h);
          const wh    = entry?.avg_wh || 0;
          const pct   = (wh / max) * 100;
          return (
            <div key={h} className="strat-hour-col"
              title={`${h}:00 – gem. ${Math.round(wh)} Wh`}>
              <div className="strat-bar-track" style={{ height: 60 }}>
                <div className="strat-bar-fill" style={{ height: `${pct}%`, background: "#60a5fa" }} />
              </div>
              <div className="strat-hour-label">{h % 3 === 0 ? `${h}h` : ""}</div>
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        Gebaseerd op InfluxDB historiek · piekuren zijn automatisch bepaald
      </div>
    </div>
  );
}

// ── Day navigation bar ────────────────────────────────────────────────────

function DayNav({ viewDate, onChange }) {
  const today    = todayStr();
  const tomorrow = tomorrowStr();
  const isDefaultView = viewDate === null;
  const displayDate   = viewDate || today;
  const atMaxFwd      = viewDate === null || displayDate >= tomorrow;
  const atMaxBack     = displayDate <= "2024-01-01";

  return (
    <div className="strat-day-nav">
      <button
        className="btn btn-ghost btn-sm"
        disabled={atMaxBack}
        onClick={() => onChange(prevDay(displayDate))}
        title="Vorige dag"
      >
        ← Vorige dag
      </button>

      <span className="strat-day-nav-label">
        {isDefaultView
          ? `Vandaag + Morgen`
          : displayDate === today
            ? `Vandaag – ${fmtDate(displayDate)}`
            : displayDate === tomorrow
              ? `Morgen – ${fmtDate(displayDate)}`
              : fmtDate(displayDate)}
      </span>

      <button
        className="btn btn-ghost btn-sm"
        disabled={atMaxFwd}
        onClick={() => {
          const next = nextDay(displayDate);
          if (next > tomorrow) { onChange(null); }
          else { onChange(next); }
        }}
        title="Volgende dag"
      >
        Volgende dag →
      </button>

      {!isDefaultView && (
        <button className="btn btn-ghost btn-sm" onClick={() => onChange(null)}>
          Vandaag
        </button>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────

// ── Automation toggle ─────────────────────────────────────────────────────

const ACTION_LABEL = {
  solar_charge: "☀️ Zonneladen",
  grid_charge:  "⚡ Netwerk laden",
  save:         "🔒 Sparen",
  discharge:    "🔋 Ontladen",
  neutral:      "· Neutraal",
};

function AutomationToggle({ planLoadedAt }) {
  const [auto,    setAuto]    = useState(null);
  const [saving,  setSaving]  = useState(false);

  const load = async () => {
    try {
      const r = await fetch("api/automation");
      if (r.ok) setAuto(await r.json());
    } catch { /* ignore */ }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  // Re-fetch immediately when the plan finishes loading (fills the plan cache)
  useEffect(() => {
    if (planLoadedAt) load();
  }, [planLoadedAt]);

  const toggle = async () => {
    if (!auto) return;
    setSaving(true);
    try {
      const r = await fetch("api/automation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !auto.enabled }),
      });
      if (r.ok) setAuto(await r.json());
    } finally { setSaving(false); }
  };

  if (!auto) return null;

  const enabled = auto.enabled;
  const action  = auto.current_action;

  return (
    <div className="automation-bar" style={{
      display: "flex", alignItems: "center", gap: 12,
      background: enabled ? "rgba(34,197,94,0.10)" : "rgba(100,116,139,0.08)",
      border: `1px solid ${enabled ? "rgba(34,197,94,0.35)" : "rgba(100,116,139,0.2)"}`,
      borderRadius: 10, padding: "10px 16px", marginBottom: 8,
    }}>
      {/* Toggle switch */}
      <button
        onClick={toggle}
        disabled={saving}
        style={{
          position: "relative", width: 44, height: 24, borderRadius: 12,
          background: enabled ? "var(--green)" : "var(--border)",
          border: "none", cursor: "pointer", transition: "background 0.2s", flexShrink: 0,
        }}
        title={enabled ? "Automatisatie uitschakelen" : "Automatisatie inschakelen"}
      >
        <span style={{
          position: "absolute", top: 3, left: enabled ? 22 : 3,
          width: 18, height: 18, borderRadius: "50%",
          background: "#fff", transition: "left 0.2s",
        }} />
      </button>

      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>
          {enabled ? "🤖 Automatisatie actief" : "🤖 Automatisatie uitgeschakeld"}
        </div>
        {enabled && (
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
            Huidig uur: <strong>{ACTION_LABEL[action] || action || "—"}</strong>
            {" · "}
            Batterijmodus:{" "}
            <strong>
              {action === "solar_charge" && "anti-feed"}
              {action === "grid_charge"  && "manual + geforceerd laden"}
              {action === "save"         && "manual + laden uit"}
              {action === "discharge"    && "anti-feed"}
              {action === "neutral"      && "anti-feed"}
              {!action                   && "—"}
            </strong>
            {auto.last_applied && (
              <>{" · "}Laatste update: {new Date(auto.last_applied).toLocaleTimeString("nl-BE")}</>
            )}
          </div>
        )}
        {!enabled && (
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
            Schakel in om batterijmodus automatisch aan te sturen op basis van de strategie.
          </div>
        )}
      </div>
    </div>
  );
}

export default function StrategyPage() {
  const [plan,          setPlan]          = useState(null);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState(null);
  const [lastFetch,     setLastFetch]     = useState(null);
  const [viewDate,      setViewDate]      = useState(null); // null = today+tomorrow, string = specific date
  const [planLoadedAt,  setPlanLoadedAt]  = useState(null);

  const load = useCallback(async (date) => {
    setLoading(true); setError(null);
    syncFlowCfgToBackend();
    try {
      const url = date ? `api/strategy/plan?date=${date}` : "api/strategy/plan";
      const r   = await fetch(url);
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.error || `HTTP ${r.status}`);
      }
      setPlan(await r.json());
      setLastFetch(new Date());
      setPlanLoadedAt(Date.now());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  // Load when viewDate changes
  useEffect(() => { load(viewDate); }, [load, viewDate]);

  // Auto-refresh every 15 minutes (only in default/today view)
  useEffect(() => {
    if (viewDate !== null) return;
    const id = setInterval(() => load(null), 15 * 60 * 1000);
    return () => clearInterval(id);
  }, [load, viewDate]);

  useEffect(() => {
    syncFlowCfgToBackend();
    const onCfgChange = () => syncFlowCfgToBackend();
    window.addEventListener("marstek_flow_cfg_changed", onCfgChange);
    return () => window.removeEventListener("marstek_flow_cfg_changed", onCfgChange);
  }, []);

  const today    = plan?.today    || [];
  const tomorrow = plan?.tomorrow || [];
  const consHours = plan?.consumption_by_hour || [];

  return (
    <div className="strat-page">
      {/* Header */}
      <div className="strat-header">
        <div>
          <div className="strat-title">🧠 Laadstrategie</div>
          <div className="strat-subtitle">
            {lastFetch && `Bijgewerkt: ${lastFetch.toLocaleTimeString("nl-BE")} · `}
            {plan && (
              <>
                {plan.prices_available
                  ? <span style={{ color: "var(--green)" }}>✓ Prijzen beschikbaar</span>
                  : <span style={{ color: "var(--text-muted)" }}>⚠ Geen prijzen (ENTSO-E configureren)</span>}
                {" · "}
                {plan.solar_available
                  ? <span style={{ color: "#fbbf24" }}>✓ Zonneprognose</span>
                  : <span style={{ color: "var(--text-muted)" }}>⚠ Geen zonneprognose (Forecast.Solar)</span>}
                {" · "}
                <span style={{ color: "var(--text-muted)" }}>
                  SOC: {Math.round(plan.soc_now || 0)}%
                </span>
                {plan.consumption_source && plan.consumption_source !== "none" && (
                  <>
                    {" · "}
                    <span style={{ color: "var(--text-dim)" }}>
                      verbruik via {{
                        external_influx: "InfluxDB (HA)",
                        local_influx:    "InfluxDB (lokaal)",
                        ha_history:      "HA history API",
                      }[plan.consumption_source] || plan.consumption_source}
                    </span>
                  </>
                )}
              </>
            )}
          </div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => load(viewDate)} disabled={loading}>
          {loading ? "Laden…" : "↺ Vernieuwen"}
        </button>
      </div>

      {/* Day navigation */}
      <DayNav viewDate={viewDate} onChange={(d) => setViewDate(d)} />

      {/* Legend */}
      <div className="strat-legend">
        {Object.entries(ACTION_COLOR).filter(([k]) => k !== "neutral").map(([k, c]) => (
          <LegendItem key={k} color={c} label={c.label} />
        ))}
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 12, height: 12, borderRadius: 3, background: "rgba(96,165,250,0.5)" }} />
          <span>Verbruik</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 12, height: 12, borderRadius: 3, background: "rgba(74,222,128,0.5)" }} />
          <span>Prijs laag</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div style={{ width: 12, height: 12, borderRadius: 3, background: "rgba(239,68,68,0.5)" }} />
          <span>Prijs hoog</span>
        </div>
      </div>

      {/* Automation toggle – only shown in forward (today) view */}
      {!viewDate && <AutomationToggle planLoadedAt={planLoadedAt} />}

      {loading && !plan && (
        <div className="loading-overlay" style={{ position: "relative", height: 100 }}>
          <div className="loading-spinner" />
          <span>Strategie berekenen…</span>
        </div>
      )}

      {error && (
        <div className="forecast-error">
          <div style={{ fontWeight: 600 }}>⚠ Fout bij laden strategie</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>{error}</div>
        </div>
      )}

      {plan && !error && (
        <>
          {plan.is_historical ? (
            /* ── Historical single-day view ── */
            <>
              <DayChart
                title={`🕐 Strategieaanbeveling – ${fmtDate(plan.date)}`}
                slots={plan.slots || []}
                isToday={false}
              />
              <ActualsChart actuals={
                plan.actuals
                  ? Object.fromEntries(
                      Object.entries(plan.actuals).map(([k, v]) => [parseInt(k), v])
                    )
                  : {}
              } />
            </>
          ) : (
            /* ── Forward view: today + tomorrow ── */
            <>
              <DayChart title="Vandaag" slots={today}    isToday={true}  />
              <DayChart title="Morgen"  slots={tomorrow} isToday={false} />
              {consHours.length > 0 && <ConsumptionProfile hours={consHours} />}
              {consHours.length === 0 && (
                <div className="strat-day-panel" style={{ color: "var(--text-muted)", fontSize: 13 }}>
                  📊 Nog geen verbruikshistoriek in InfluxDB. Het profiel wordt automatisch opgebouwd
                  naarmate data binnenkomt. Manuele piekuren zijn in te stellen via Instellingen → Laadstrategie.
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
