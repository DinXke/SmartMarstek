/**
 * Aggregated home power flow – Lumina-inspired design.
 *
 * Layout (when solar configured):
 *           [☀️ SOLAR]
 *                ↕
 *   [⚡ NET] ←──→ [🏠 HUIS] ←──→ [🔋 BAT]
 *
 * Props:
 *   batteries     – [{ id, name, acPower, batPower, soc, l1V, l2V, l3V, acVoltage }]
 *   phaseVoltages – { L1, L2, L3 } fallback from first ESPHome device
 *   acVoltage     – V fallback
 *
 * Flow config is read from localStorage "marstek_flow_cfg".
 * HomeWizard sensor values are fetched from /api/homewizard/data every 10 s.
 */

import { useState, useEffect, useCallback } from "react";
import { loadFlowCfg } from "./FlowSourcesSettings.jsx";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(w, showSign = false) {
  if (w == null) return "—";
  const abs = Math.abs(w);
  const sign = showSign && w > 0 ? "+" : "";
  if (abs >= 1000) return `${sign}${w < 0 ? "-" : showSign ? "+" : ""}${(abs / 1000).toFixed(2)} kW`;
  return `${sign}${Math.round(w)} W`;
}

function fmtPct(v) {
  if (v == null) return null;
  return `${v.toFixed(0)}%`;
}

/** Derive animation speed from power magnitude */
function flowDur(power) {
  const abs = Math.abs(power ?? 0);
  if (abs < 50)   return "2.5s";
  if (abs < 500)  return "1.8s";
  if (abs < 2000) return "1.2s";
  return "0.8s";
}

/** Resolve one source entry → numeric value or null */
function resolveOne(sc, batteries, hwData, haData) {
  if (sc.source === "esphome") {
    const bat = batteries.find((b) => b.id === sc.device_id);
    const v = bat?.[sc.sensor];
    if (v == null) return null;
    return sc.invert ? -v : v;
  }
  if (sc.source === "homewizard") {
    const dev = hwData?.devices?.find((d) => d.id === sc.device_id);
    const sensor = dev?.sensors?.[sc.sensor];
    if (sensor?.value == null) return null;
    return sc.invert ? -sensor.value : sensor.value;
  }
  if (sc.source === "homeassistant") {
    const entry = haData?.[sc.sensor];
    if (entry?.value == null) return null;
    return sc.invert ? -entry.value : entry.value;
  }
  return null;
}

/**
 * Resolve a flow slot: supports array of sources (summed) or single object (backward compat).
 * For bat_soc the values are averaged instead of summed.
 */
function resolveSlot(key, cfg, batteries, hwData, haData) {
  let slotCfg = cfg?.[key];
  if (!slotCfg) return null;
  if (!Array.isArray(slotCfg)) slotCfg = [slotCfg]; // backward compat

  const isAvg = key === "bat_soc";
  let total = null;
  let count = 0;

  for (const sc of slotCfg) {
    const v = resolveOne(sc, batteries, hwData, haData);
    if (v != null) {
      total = (total ?? 0) + v;
      count++;
    }
  }

  if (total == null) return null;
  return isAvg && count > 0 ? total / count : total;
}

// ---------------------------------------------------------------------------
// SVG sub-components
// ---------------------------------------------------------------------------

function GlowArrow({ x1, y1, x2, y2, color, active, reverse, power }) {
  if (!active) {
    return (
      <line x1={x1} y1={y1} x2={x2} y2={y2}
        stroke="#1e293b" strokeWidth={3} strokeLinecap="round" />
    );
  }

  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy);
  const ux = dx / len, uy = dy / len;
  const as = 7;
  const px = -uy * as * 0.55, py = ux * as * 0.55;
  const dur = flowDur(power);

  return (
    <g>
      <line x1={x1} y1={y1} x2={x2} y2={y2}
        stroke="#1e293b" strokeWidth={3} strokeLinecap="round" />
      <g filter="url(#flow-glow)">
        <line x1={x1} y1={y1} x2={x2} y2={y2}
          stroke={color} strokeWidth={3} strokeDasharray="8 6"
          strokeLinecap="round" opacity={0.9}>
          <animate attributeName="stroke-dashoffset"
            from={reverse ? "0" : "56"} to={reverse ? "56" : "0"}
            dur={dur} repeatCount="indefinite" />
        </line>
        <polygon
          points={`${x2},${y2} ${x2 - ux * as + px},${y2 - uy * as + py} ${x2 - ux * as - px},${y2 - uy * as - py}`}
          fill={color} opacity={0.9} />
      </g>
    </g>
  );
}

function GlowNode({ cx, cy, r, icon, label, color, active, sublabel, sublabelColor }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill="#0f172a" stroke={color}
        strokeWidth={active ? 2 : 1.5} opacity={active ? 1 : 0.55} />
      {active && (
        <circle cx={cx} cy={cy} r={r} fill={color} opacity={0.08}
          filter="url(#flow-glow)" />
      )}
      <text x={cx} y={cy - 6} textAnchor="middle" dominantBaseline="middle"
        fontSize={r >= 28 ? 17 : 15}>{icon}</text>
      <text x={cx} y={cy + 11} textAnchor="middle" dominantBaseline="middle"
        fill={active ? "#94a3b8" : "#475569"} fontSize={7.5}
        fontFamily="Inter, system-ui, sans-serif" letterSpacing="0.5">
        {label}
      </text>
      {sublabel && (
        <text x={cx} y={cy + r + 14} textAnchor="middle" dominantBaseline="middle"
          fill={sublabelColor || "#64748b"} fontSize={9} fontWeight="600"
          fontFamily="Inter, system-ui, sans-serif">
          {sublabel}
        </text>
      )}
    </g>
  );
}

function FlowLabel({ x, y, text, color, small }) {
  return (
    <text x={x} y={y} textAnchor="middle" dominantBaseline="middle"
      fill={color} fontSize={small ? 8 : 10} fontWeight={small ? "400" : "600"}
      fontFamily="Inter, system-ui, sans-serif">
      {text}
    </text>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function HomeFlow({ batteries = [], phaseVoltages, acVoltage }) {
  const [hwData,  setHwData]  = useState(null);
  const [haData,  setHaData]  = useState({});  // {entity_id: {value, unit}}
  const [cfg,     setCfg]     = useState(() => loadFlowCfg());

  // Reload config when settings page saves it
  useEffect(() => {
    const refresh = () => setCfg(loadFlowCfg());
    window.addEventListener("marstek_flow_cfg_changed", refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener("marstek_flow_cfg_changed", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  const pollHw = useCallback(async () => {
    try {
      const r = await fetch("/api/homewizard/data");
      if (r.ok) setHwData(await r.json());
    } catch { /* no HW configured */ }
  }, []);

  // Poll all HA entity_ids that are referenced in the current config
  const pollHa = useCallback(async (currentCfg) => {
    const entityIds = Object.values(currentCfg)
      .filter((sc) => sc?.source === "homeassistant" && sc.sensor)
      .map((sc) => sc.sensor);
    if (!entityIds.length) return;
    try {
      const r = await fetch("/api/ha/poll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_ids: entityIds }),
      });
      if (r.ok) setHaData(await r.json());
    } catch { /* HA not configured */ }
  }, []);

  useEffect(() => {
    pollHw();
    pollHa(cfg);
    const id = setInterval(() => { pollHw(); pollHa(cfg); }, 10000);
    return () => clearInterval(id);
  }, [pollHw, pollHa, cfg]);

  // ── Aggregate ESPHome defaults ─────────────────────────────────────────────
  let totalAc = null, totalBat = null;
  for (const b of batteries) {
    if (b.acPower  != null) totalAc  = (totalAc  ?? 0) + b.acPower;
    if (b.batPower != null) totalBat = (totalBat ?? 0) + b.batPower;
  }

  // Average SOC across batteries
  const socsWithData = batteries.map((b) => b.soc).filter((v) => v != null);
  const avgSoc = socsWithData.length > 0
    ? socsWithData.reduce((a, v) => a + v, 0) / socsWithData.length
    : null;

  // ── Resolve configured slots ───────────────────────────────────────────────
  const solarPower  = resolveSlot("solar_power", cfg, batteries, hwData, haData);

  // net_power: positive = import from grid
  const netPowerRaw = resolveSlot("net_power",   cfg, batteries, hwData, haData);

  // bat_power: positive = discharging
  const batPowerRaw = resolveSlot("bat_power",   cfg, batteries, hwData, haData);

  const batSoc = resolveSlot("bat_soc", cfg, batteries, hwData, haData) ?? avgSoc;

  // Phase voltages overrides
  const v1 = resolveSlot("voltage_l1", cfg, batteries, hwData, haData);
  const v2 = resolveSlot("voltage_l2", cfg, batteries, hwData, haData);
  const v3 = resolveSlot("voltage_l3", cfg, batteries, hwData, haData);

  // ── Unified sign convention ────────────────────────────────────────────────
  // netDisplayPower: positive = export to grid (drives arrow direction logic)
  //   – when override: negate (import→export convention)
  //   – fallback: totalAc (ESPHome: positive = export)
  const netDisplayPower = netPowerRaw != null ? -netPowerRaw : totalAc;

  // batDisplayPower: positive = discharging (consistent with both sources)
  const batDisplayPower = batPowerRaw ?? totalBat;

  // House consumption = discharge + grid import = batDisplay - netDisplay
  const housePower = (netDisplayPower != null || batDisplayPower != null)
    ? (batDisplayPower ?? 0) - (netDisplayPower ?? 0) + (solarPower ?? 0)
    : null;

  // Phase voltages
  const ePV = (v1 != null || v2 != null || v3 != null)
    ? { L1: v1 ?? phaseVoltages?.L1, L2: v2 ?? phaseVoltages?.L2, L3: v3 ?? phaseVoltages?.L3 }
    : phaseVoltages;

  const phaseStr = ePV
    ? [
        ePV.L1 != null ? `L1:${ePV.L1.toFixed(0)}V` : null,
        ePV.L2 != null ? `L2:${ePV.L2.toFixed(0)}V` : null,
        ePV.L3 != null ? `L3:${ePV.L3.toFixed(0)}V` : null,
      ].filter(Boolean).join("  ")
    : acVoltage != null ? `${acVoltage.toFixed(1)} V` : null;

  // ── Arrow and color logic ──────────────────────────────────────────────────
  // Net ↔ Huis (netDisplayPower: positive = export to grid)
  const netActive = netDisplayPower != null && Math.abs(netDisplayPower) > 5;
  const netToGrid = (netDisplayPower ?? 0) > 0;     // exporting → arrow flows right-to-left
  const netColor  = netActive ? (netToGrid ? "#22c55e" : "#ef4444") : "#334155";

  // Bat ↔ Huis (batDisplayPower: positive = discharging)
  const batActive = batDisplayPower != null && Math.abs(batDisplayPower) > 5;
  const batDisch  = (batDisplayPower ?? 0) > 0;
  const batColor  = batActive ? (batDisch ? "#f59e0b" : "#3b82f6") : "#334155";

  // Solar ↓ Huis
  const solarActive = solarPower != null && solarPower > 5;
  const solarColor  = "#fbbf24";

  // SOC color
  const socColor = batSoc == null ? "#64748b"
    : batSoc < 20 ? "#ef4444"
    : batSoc < 50 ? "#f59e0b"
    : "#22c55e";

  const houseColor = housePower != null && housePower > 10 ? "#a78bfa" : "#475569";

  // ── SVG layout ─────────────────────────────────────────────────────────────
  const W = 440, H = 165;
  const midY = 112;
  const netX = 55, huisX = 220, batX = 385;
  const solX = 220, solY = 26, solR = 22, nodeR = 28;

  // Arrow endpoints (edge of nodes)
  const netArrowX1 = netX  + nodeR, netArrowX2 = huisX - nodeR;
  const batArrowX1 = huisX + nodeR, batArrowX2 = batX  - nodeR;
  const solArrowY1 = solY  + solR,  solArrowY2 = midY  - nodeR;

  const netLabelX = (netArrowX1 + netArrowX2) / 2;
  const batLabelX = (batArrowX1 + batArrowX2) / 2;

  // Label values: use raw import/export value where possible for clarity
  const netLabelValue = netPowerRaw != null ? netPowerRaw : totalAc;  // positive = import if override, positive = export if fallback
  const batLabelValue = batDisplayPower;

  return (
    <div className="home-flow-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="home-flow-svg" aria-label="Vermogensbalans">
        <defs>
          <filter id="flow-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* ── Arrows ── */}

        {/* Solar ↓ Huis (only when solar configured) */}
        {solarPower != null && (
          <GlowArrow x1={solX} y1={solArrowY1} x2={huisX} y2={solArrowY2}
            color={solarColor} active={solarActive} reverse={false} power={solarPower} />
        )}

        {/* Net ↔ Huis */}
        <GlowArrow x1={netArrowX1} y1={midY} x2={netArrowX2} y2={midY}
          color={netColor} active={netActive} reverse={netToGrid} power={netDisplayPower} />

        {/* Huis ↔ Bat */}
        <GlowArrow x1={batArrowX1} y1={midY} x2={batArrowX2} y2={midY}
          color={batColor} active={batActive} reverse={!batDisch} power={batDisplayPower} />

        {/* ── Power labels ── */}

        {/* Net link label */}
        <FlowLabel x={netLabelX} y={midY - 15}
          text={fmt(netLabelValue)}
          color={netActive ? netColor : "#475569"} />

        {/* Bat link label */}
        <FlowLabel x={batLabelX} y={midY - 15}
          text={fmt(batLabelValue)}
          color={batActive ? batColor : "#475569"} />

        {/* Solar label */}
        {solarPower != null && (
          <FlowLabel x={solX - 38} y={(solArrowY1 + solArrowY2) / 2}
            text={fmt(solarPower)}
            color={solarActive ? solarColor : "#475569"} />
        )}

        {/* Phase voltages below Net link */}
        {phaseStr && (
          <FlowLabel x={netLabelX} y={midY + 16}
            text={phaseStr} color="#64748b" small />
        )}

        {/* ── Nodes ── */}

        {/* Solar (only when configured) */}
        {solarPower != null && (
          <GlowNode cx={solX} cy={solY} r={solR} icon="☀️" label="SOLAR"
            color={solarColor} active={solarActive} />
        )}

        {/* Net */}
        <GlowNode cx={netX} cy={midY} r={nodeR} icon="⚡" label="NET"
          color={netToGrid ? "#22c55e" : "#ef4444"} active={netActive} />

        {/* Huis */}
        <GlowNode cx={huisX} cy={midY} r={nodeR} icon="🏠" label="HUIS"
          color="#a78bfa" active={housePower != null && housePower > 10}
          sublabel={housePower != null ? `${fmt(housePower)} verbruik` : null}
          sublabelColor={houseColor} />

        {/* Bat */}
        <GlowNode cx={batX} cy={midY} r={nodeR} icon="🔋" label="BATTERIJEN"
          color={batDisch ? "#f59e0b" : "#3b82f6"} active={batActive}
          sublabel={batSoc != null ? fmtPct(batSoc) : null}
          sublabelColor={socColor} />
      </svg>

      {/* Per-battery breakdown */}
      {batteries.length > 1 && (
        <div className="home-flow-breakdown">
          {batteries.map((b) => {
            // When bat_power override active, show device + SOC only
            if (batPowerRaw != null) {
              return (
                <div key={b.id} className="hfb-item">
                  <span className="hfb-name">{b.name}</span>
                  {b.soc != null && (
                    <span style={{ fontFamily: "monospace", fontSize: 11, color: socColor }}>
                      {b.soc.toFixed(0)}%
                    </span>
                  )}
                </div>
              );
            }
            const pwr = b.batPower;
            const cls = pwr == null ? "" : pwr > 5 ? "hfb-discharge" : pwr < -5 ? "hfb-charge" : "";
            return (
              <div key={b.id} className={`hfb-item ${cls}`}>
                <span className="hfb-name">{b.name}</span>
                <span className="hfb-power">{fmt(pwr, true)}</span>
                {b.soc != null && (
                  <span style={{ fontSize: 10, color: "#64748b", fontFamily: "monospace" }}>
                    {b.soc.toFixed(0)}%
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
