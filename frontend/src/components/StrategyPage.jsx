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

// ── Group consecutive slots by action, collapsible ───────────────────────

function SlotRow({ slot }) {
  const ac    = ACTION_COLOR[slot.action] || ACTION_COLOR.neutral;
  const isNeu = slot.action === "neutral";
  return (
    <div className="strat-detail-row"
      style={{ borderLeft: `3px solid ${ac.border}`, opacity: isNeu ? 0.45 : 1 }}>
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
}

function SlotGroup({ group, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const ac    = ACTION_COLOR[group.action] || ACTION_COLOR.neutral;
  const isNeu = group.action === "neutral";
  const first = group.slots[0];
  const last  = group.slots[group.slots.length - 1];

  // Single slot: render directly without toggle
  if (group.slots.length === 1) return <SlotRow slot={first} />;

  return (
    <div>
      {/* Group header */}
      <div
        className="strat-detail-row strat-group-header"
        style={{ borderLeft: `3px solid ${ac.border}`, opacity: isNeu ? 0.6 : 1, cursor: "pointer" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="strat-detail-time">
          {fmtHour(first.time)}–{String((last.hour + 1) % 24).padStart(2, "0")}:00
        </span>
        <span className="strat-detail-icon">{ac.icon}</span>
        <span className="strat-detail-action" style={{ color: ac.border }}>
          {ac.label}
          <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.7 }}>
            ({group.slots.length}u)
          </span>
        </span>
        <span className="strat-detail-reason" style={{ opacity: 0.6 }}>
          {Math.round(first.soc_start)}% → {Math.round(last.soc_end)}%
        </span>
        <span style={{ marginLeft: "auto", fontSize: 13, color: "var(--text-muted)", paddingRight: 4 }}>
          {open ? "▲" : "▼"}
        </span>
      </div>
      {/* Expanded rows */}
      {open && group.slots.map((s) => <SlotRow key={s.time} slot={s} />)}
    </div>
  );
}

function SlotGroupList({ slots, isToday, nowHour }) {
  // Build groups of consecutive same-action slots
  const groups = [];
  for (const slot of slots) {
    const last = groups[groups.length - 1];
    if (last && last.action === slot.action) {
      last.slots.push(slot);
    } else {
      groups.push({ action: slot.action, slots: [slot] });
    }
  }

  // A group is open by default if it contains the current hour, or if it's
  // a non-neutral action (interesting to see)
  return (
    <div className="strat-detail-list">
      {groups.map((g, i) => {
        const containsNow = isToday && g.slots.some((s) => s.hour === nowHour && !s.is_past);
        const defaultOpen = containsNow;
        return <SlotGroup key={i} group={g} defaultOpen={defaultOpen} />;
      })}
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

      {slots.length > 0 && (
        <SlotGroupList slots={slots} isToday={isToday} nowHour={nowHour} />
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

const WD_NAMES = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"];

function ConsumptionProfile({ hours, standbyW = 0 }) {
  if (!hours || !hours.length) return null;

  const hasWdData = hours.some((h) => h.weekday !== undefined);
  // JS getDay(): 0=Sun…6=Sat → Python weekday 0=Mon…6=Sun
  const todayWd = (new Date().getDay() + 6) % 7;
  const [selWd, setSelWd] = useState(todayWd);

  const filtered = hasWdData ? hours.filter((h) => h.weekday === selWd) : hours;
  const max = Math.max(1, ...filtered.map((h) => h.avg_wh));

  return (
    <div className="strat-day-panel">
      <div className="strat-day-title" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span>📊 Verbruiksprofiel (historiek)</span>
        {hasWdData && (
          <div style={{ display: "flex", gap: 4 }}>
            {WD_NAMES.map((name, wd) => (
              <button key={wd} type="button" onClick={() => setSelWd(wd)}
                style={{
                  padding: "2px 7px", borderRadius: 5, fontSize: 11, cursor: "pointer",
                  border: "1px solid", borderColor: selWd === wd ? "var(--accent)" : "var(--border)",
                  background: selWd === wd ? "var(--accent)" : "transparent",
                  color: selWd === wd ? "#fff" : "var(--text)",
                  fontWeight: wd === todayWd ? 700 : 400,
                }}>
                {name}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="strat-chart" style={{ marginTop: 8 }}>
        {Array.from({ length: 24 }, (_, h) => {
          const entry = filtered.find((x) => x.hour === h);
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
      {standbyW > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          Sluipverbruik: <strong style={{ color: "var(--text)" }}>{Math.round(standbyW)} W</strong>
          {" "}(gem. 02–06u) · piekuren bepaald op verbruik bóven sluipverbruik
        </div>
      )}
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
        {hasWdData
          ? `Profiel voor ${WD_NAMES[selWd]}${selWd === todayWd ? " (vandaag)" : ""} · piekuren per weekdag`
          : "Gebaseerd op historiek · piekuren automatisch bepaald"}
      </div>
    </div>
  );
}

// ── Forecast bias / confidence panel ─────────────────────────────────────

function BiasPanel() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetch("api/accuracy/summary")
      .then((r) => (r.status === 204 ? null : r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {});
  }, []);

  if (!data) return null;

  const { solar_forecast, consumption_forecast, confidence_pct, records_analysed,
          solar_factor, cons_factor } = data;

  function biasColor(bias) {
    if (bias == null) return "var(--text-muted)";
    const abs = Math.abs(bias);
    if (abs < 5)  return "#22c55e";
    if (abs < 15) return "#eab308";
    return "#ef4444";
  }

  function biasBadge(bias, factor) {
    if (bias == null) return "—";
    const direction = bias > 0 ? "te hoog" : "te laag";
    const corrected = factor != null && Math.abs(factor - 1.0) > 0.005
      ? ` → gecorrigeerd ×${factor.toFixed(2)}`
      : "";
    return `${bias > 0 ? "+" : ""}${bias.toFixed(1)}% (${direction})${corrected}`;
  }

  const confColor = confidence_pct == null ? "var(--text-muted)"
    : confidence_pct >= 80 ? "#22c55e"
    : confidence_pct >= 60 ? "#eab308"
    : "#ef4444";

  return (
    <div className="strat-day-panel" style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <span className="strat-day-title">📐 Prognose-kwaliteit &amp; bias-correctie</span>
        {confidence_pct != null && (
          <span style={{ fontSize: 13, fontWeight: 700, color: confColor }}>
            {confidence_pct.toFixed(0)}% betrouwbaarheid
          </span>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8, marginTop: 8 }}>
        {solar_forecast && solar_forecast.n > 0 && (
          <div style={{ background: "rgba(251,191,36,0.07)", borderRadius: 8,
            border: "1px solid rgba(251,191,36,0.25)", padding: "10px 14px" }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>
              ☀️ Zonprognose bias ({solar_forecast.n} uur)
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, color: biasColor(solar_forecast.avg_bias_pct) }}>
              {biasBadge(solar_forecast.avg_bias_pct, solar_factor)}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
              MAE: {solar_forecast.mae_pct?.toFixed(1)}%
            </div>
          </div>
        )}
        {consumption_forecast && consumption_forecast.n > 0 && (
          <div style={{ background: "rgba(96,165,250,0.07)", borderRadius: 8,
            border: "1px solid rgba(96,165,250,0.25)", padding: "10px 14px" }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>
              🏠 Verbruiksprognose bias ({consumption_forecast.n} uur)
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, color: biasColor(consumption_forecast.avg_bias_pct) }}>
              {biasBadge(consumption_forecast.avg_bias_pct, cons_factor)}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
              MAE: {consumption_forecast.mae_pct?.toFixed(1)}%
            </div>
          </div>
        )}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
        Op basis van {records_analysed} vergelijkingen (30d) · correctie automatisch toegepast op toekomstige slots
      </div>
    </div>
  );
}

// ── Claude usage stats panel ─────────────────────────────────────────────

function ClaudeStatsPanel() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    const load = () =>
      fetch("api/claude/usage")
        .then((r) => r.ok ? r.json() : null)
        .then(setStats)
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (!stats) return null;

  const fmtEur = (e) => e == null ? "€0"
    : e < 0.0001 ? `${(e * 100).toFixed(4)} ct`
    : e < 0.01   ? `${(e * 100).toFixed(3)} ct`
    : `€${e.toFixed(4)}`;

  const rows = [
    { label: "Laatste 24 uur", s: stats.last_1d },
    { label: "Laatste 7 dagen", s: stats.last_7d },
    { label: "Laatste 31 dagen", s: stats.last_31d },
    { label: "Totaal ooit",      s: stats.all_time },
  ];

  return (
    <div className="strat-day-panel">
      <div className="strat-day-title">🤖 Claude AI — gebruik &amp; kosten</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8, marginTop: 8 }}>
        {rows.map(({ label, s }) => (
          <div key={label} style={{
            background: "rgba(99,102,241,0.07)", borderRadius: 8,
            border: "1px solid rgba(99,102,241,0.2)", padding: "10px 14px",
          }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>{label}</div>
            {s && s.calls > 0 ? (
              <>
                <div style={{ fontSize: 18, fontWeight: 700, color: "#818cf8" }}>
                  {fmtEur(s.eur)}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  {s.calls} {s.calls === 1 ? "aanroep" : "aanroepen"}
                  {" · "}{((s.tokens_in + s.tokens_out) / 1000).toFixed(1)}k tokens
                </div>
              </>
            ) : (
              <div style={{ fontSize: 13, color: "var(--text-muted)" }}>—</div>
            )}
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
        Roldende vensters (24u / 7d / 31d). Prijzen gebaseerd op Claude Haiku 4.5 ($0.80/$4.00 per MTok).
        Claude wordt enkel aangeroepen bij nieuwe prijzen (~1×/dag).
      </div>
    </div>
  );
}

// ── Claude debug panel ────────────────────────────────────────────────────

const ACTION_LABEL_SHORT = {
  solar_charge: "☀️ Zonneladen",
  grid_charge:  "⚡ Laden",
  save:         "🔒 Sparen",
  discharge:    "🔋 Ontladen",
  neutral:      "· Neutraal",
};

function ClaudeDebugPanel({ debug, plan }) {
  const [open,  setOpen]  = useState(false);
  const [usage, setUsage] = useState(null);

  useEffect(() => {
    fetch("api/claude/usage")
      .then((r) => r.ok ? r.json() : null)
      .then(setUsage)
      .catch(() => {});
  }, [debug?.ran_at]);  // re-fetch whenever a new run happens

  if (!debug) return null;

  const modelShort = (debug.model || "")
    .replace("claude-", "")
    .replace("-20251001", "")
    .replace("-20240229", "");
  // Haiku 4.5: $0.80/MTok in, $4.00/MTok out (convert to EUR ≈ ×0.92)
  const costEur = debug.input_tokens && debug.output_tokens
    ? (debug.input_tokens * 0.00000080 + debug.output_tokens * 0.000004) * 0.92
    : null;
  const costStr = costEur != null
    ? costEur < 0.001 ? `~${(costEur * 100).toFixed(3)} ct` : `~€${costEur.toFixed(4)}`
    : null;
  const fp = plan?.price_fingerprint;

  return (
    <div style={{
      background: debug.fallback ? "rgba(248,113,113,0.08)" : "rgba(99,102,241,0.08)",
      border: `1px solid ${debug.fallback ? "rgba(248,113,113,0.3)" : "rgba(99,102,241,0.3)"}`,
      borderRadius: 10, padding: "10px 14px", marginBottom: 8, fontSize: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, color: debug.fallback ? "var(--red)" : "#818cf8" }}>
          {debug.fallback ? "⚠ Claude AI (fallback naar regelgebaseerd)" : "🤖 Claude AI"}
        </span>
        {!debug.fallback && (
          <>
            <span style={{ color: "var(--text-muted)" }}>Model: <strong style={{ color: "var(--text)" }}>{modelShort}</strong></span>
            {debug.ran_at && (
              <span style={{ color: "var(--text-muted)" }}>
                Laatste berekening:{" "}
                <strong style={{ color: "var(--text)" }}>
                  {new Date(debug.ran_at).toLocaleString("nl-BE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                </strong>
              </span>
            )}
            <span style={{ color: "var(--text-muted)" }}>
              Tokens: <strong style={{ color: "var(--text)" }}>{debug.input_tokens ?? "?"} in / {debug.output_tokens ?? "?"} out</strong>
            </span>
            {costStr && (
              <span style={{ color: "var(--text-muted)" }}>
                Kosten: <strong style={{ color: "var(--text)" }}>{costStr}</strong>
              </span>
            )}
            <span style={{ color: "var(--text-muted)" }}>
              Tijd: <strong style={{ color: "var(--text)" }}>{debug.elapsed_s}s</strong>
            </span>
            {fp && (
              <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                prijzen-fp: <code style={{ color: "var(--text-dim)" }}>{fp}</code>
                {" · "}Claude enkel herberekend bij nieuwe prijzen
              </span>
            )}
            {usage && (
              <span style={{
                marginLeft: "auto", fontSize: 11, color: "var(--text-muted)",
                display: "flex", gap: 10, flexWrap: "wrap",
              }}>
                {[
                  ["Vandaag",    usage.today],
                  ["Deze week",  usage.this_week],
                  ["Deze maand", usage.this_month],
                ].map(([label, s]) => s && s.calls > 0 && (
                  <span key={label}>
                    {label}:{" "}
                    <strong style={{ color: "var(--text)" }}>
                      {s.calls}× · {s.eur < 0.001
                        ? `${(s.eur * 100).toFixed(3)} ct`
                        : `€${s.eur.toFixed(4)}`}
                    </strong>
                  </span>
                ))}
              </span>
            )}
            {debug.action_counts && (
              <span style={{ color: "var(--text-muted)" }}>
                {Object.entries(debug.action_counts)
                  .filter(([, n]) => n > 0)
                  .map(([a, n]) => `${ACTION_LABEL_SHORT[a] || a}: ${n}u`)
                  .join(" · ")}
              </span>
            )}
          </>
        )}
        {debug.fallback && (
          <span style={{ color: "var(--red)" }}>{debug.fallback_reason}</span>
        )}
        {!debug.fallback && debug.slot_reasoning?.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            style={{
              marginLeft: "auto", fontSize: 11, padding: "2px 10px", borderRadius: 5,
              border: "1px solid rgba(99,102,241,0.4)", background: "transparent",
              color: "#818cf8", cursor: "pointer",
            }}>
            {open ? "Verberg redenering ▲" : "Toon redenering ▼"}
          </button>
        )}
      </div>

      {open && debug.slot_reasoning?.length > 0 && (
        <div style={{
          marginTop: 10, maxHeight: 320, overflowY: "auto",
          borderTop: "1px solid rgba(99,102,241,0.2)", paddingTop: 8,
          display: "flex", flexDirection: "column", gap: 2,
        }}>
          {debug.slot_reasoning.map((item, i) => {
            const ac = ACTION_COLOR[item.action] || ACTION_COLOR.neutral;
            return (
              <div key={i} style={{
                display: "flex", gap: 8, alignItems: "baseline",
                padding: "2px 0", borderBottom: "1px solid rgba(255,255,255,0.04)",
              }}>
                <span style={{ color: "var(--text-muted)", minWidth: 38, fontSize: 11 }}>
                  {item.time ? item.time.slice(11, 16) : ""}
                </span>
                <span style={{
                  minWidth: 90, fontSize: 11, fontWeight: 600, color: ac.border,
                }}>
                  {ac.icon} {ac.label}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{item.reason}</span>
              </div>
            );
          })}
        </div>
      )}
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

  const enabled        = auto.enabled;
  const action         = auto.current_action;
  const lastAction     = auto.last_action;
  const overrideReason = auto.override_reason;

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
              {lastAction === "solar_charge" && "anti-feed"}
              {lastAction === "grid_charge"  && "manual + geforceerd laden"}
              {lastAction === "save"         && "anti-feed"}
              {lastAction === "discharge"    && "anti-feed"}
              {lastAction === "neutral"      && "anti-feed"}
              {!lastAction                   && "—"}
            </strong>
            {auto.last_applied && (
              <>{" · "}Laatste update: {new Date(auto.last_applied).toLocaleTimeString("nl-BE")}</>
            )}
          </div>
        )}
        {enabled && overrideReason && (
          <div style={{
            fontSize: 12, marginTop: 4, padding: "3px 8px", borderRadius: 5,
            background: "rgba(251,191,36,0.15)", border: "1px solid rgba(251,191,36,0.4)",
            color: "#fbbf24",
          }}>
            ⚡ Override: {overrideReason}
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

  const load = useCallback(async (date, force = false) => {
    setLoading(true); setError(null);
    syncFlowCfgToBackend();
    try {
      let url = date ? `api/strategy/plan?date=${date}` : "api/strategy/plan";
      if (force) url += (url.includes("?") ? "&" : "?") + "refresh=1";
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
  const standbyW  = plan?.standby_w || 0;

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
                {" · "}
                {plan.strategy_engine === "claude"
                  ? <span style={{ color: "#818cf8", fontWeight: 600 }}>🤖 Claude AI{plan.claude_debug?.model ? ` (${plan.claude_debug.model.replace("claude-","").replace("-20251001","")})` : ""}</span>
                  : <span style={{ color: "var(--text-muted)" }}>⚙️ Regelgebaseerd</span>
                }
              </>
            )}
          </div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => load(viewDate, true)} disabled={loading}>
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

      {/* Forecast bias / confidence panel */}
      <BiasPanel />

      {/* Claude debug panel – shown when Claude engine was used */}
      {plan?.claude_debug && <ClaudeDebugPanel debug={plan.claude_debug} plan={plan} />}

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
              {consHours.length > 0 && <ConsumptionProfile hours={consHours} standbyW={standbyW} />}
              {consHours.length === 0 && (
                <div className="strat-day-panel" style={{ color: "var(--text-muted)", fontSize: 13 }}>
                  📊 Nog geen verbruikshistoriek in InfluxDB. Het profiel wordt automatisch opgebouwd
                  naarmate data binnenkomt. Manuele piekuren zijn in te stellen via Instellingen → Laadstrategie.
                </div>
              )}
              <ClaudeStatsPanel />
            </>
          )}
        </>
      )}
    </div>
  );
}
