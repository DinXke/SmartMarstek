/**
 * Per-device animated power-flow: Grid ←→ Inverter ←→ Battery
 *
 * Props:
 *   acPower       – W  (positive = export to grid)
 *   batteryPower  – W  (positive = discharging)
 *   acVoltage     – V  single-phase fallback
 *   phaseVoltages – { L1, L2, L3 }  optional
 */

function fmt(w) {
  if (w == null) return "—";
  const abs = Math.abs(w);
  if (abs >= 1000) return `${(abs / 1000).toFixed(2)} kW`;
  return `${Math.round(abs)} W`;
}

function Arrow({ x1, y1, x2, y2, color, active, reverse }) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy);
  const ux = dx / len, uy = dy / len;
  const pad = 34;
  const sx = x1 + ux * pad, sy = y1 + uy * pad;
  const ex = x2 - ux * pad, ey = y2 - uy * pad;
  const as = 6;
  const ax1 = ex - ux * as - uy * as * 0.6, ay1 = ey - uy * as + ux * as * 0.6;
  const ax2 = ex - ux * as + uy * as * 0.6, ay2 = ey - uy * as - ux * as * 0.6;
  return (
    <g>
      <line x1={sx} y1={sy} x2={ex} y2={ey} stroke="#1e293b" strokeWidth={2.5} />
      {active && (
        <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={color} strokeWidth={2.5}
          strokeDasharray="6 8" opacity={0.85}>
          <animate attributeName="stroke-dashoffset"
            from={reverse ? "0" : "56"} to={reverse ? "56" : "0"}
            dur="1.2s" repeatCount="indefinite" />
        </line>
      )}
      {active && (
        <polygon points={`${ex},${ey} ${ax1},${ay1} ${ax2},${ay2}`} fill={color} opacity={0.9} />
      )}
    </g>
  );
}

function Node({ cx, cy, icon, label, color }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={28} fill="#0f172a" stroke={color} strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={28} fill={color} opacity={0.06} />
      <text x={cx} y={cy - 5} textAnchor="middle" dominantBaseline="middle" fontSize={16}>{icon}</text>
      <text x={cx} y={cy + 11} textAnchor="middle" dominantBaseline="middle"
        fill="#94a3b8" fontSize={8} fontFamily="Inter, system-ui, sans-serif" letterSpacing="0.5">
        {label}
      </text>
    </g>
  );
}

export default function PowerFlow({ acPower, batteryPower, acVoltage, phaseVoltages }) {
  const W = 300, H = 90;
  const gridX = 40, invX = W / 2, batX = W - 40, midY = H / 2;

  const acActive  = acPower != null && Math.abs(acPower) > 5;
  const acToGrid  = (acPower ?? 0) > 0;
  const acColor   = acActive ? (acToGrid ? "#f59e0b" : "#22c55e") : "#334155";

  const batActive = batteryPower != null && Math.abs(batteryPower) > 5;
  const invToBat  = (batteryPower ?? 0) < 0;
  const batColor  = batActive ? (invToBat ? "#3b82f6" : "#f59e0b") : "#334155";

  const phaseStr = phaseVoltages
    ? [
        phaseVoltages.L1 != null ? `L1:${phaseVoltages.L1.toFixed(0)}V` : null,
        phaseVoltages.L2 != null ? `L2:${phaseVoltages.L2.toFixed(0)}V` : null,
        phaseVoltages.L3 != null ? `L3:${phaseVoltages.L3.toFixed(0)}V` : null,
      ].filter(Boolean).join("  ")
    : acVoltage != null ? `${acVoltage.toFixed(1)} V` : null;

  return (
    <div className="power-flow">
      <svg className="power-flow-svg" viewBox={`0 0 ${W} ${H}`} aria-label="Vermogensflow">

        <Arrow x1={gridX} y1={midY} x2={invX} y2={midY}
          color={acColor} active={acActive} reverse={!acToGrid} />
        <Arrow x1={invX} y1={midY} x2={batX} y2={midY}
          color={batColor} active={batActive} reverse={!invToBat} />

        {/* Power labels above links */}
        <text x={(gridX + invX) / 2} y={midY - 14} textAnchor="middle" dominantBaseline="middle"
          fill={acActive ? acColor : "#475569"} fontSize={10} fontWeight="600"
          fontFamily="Inter, system-ui, sans-serif">
          {fmt(acPower)}
        </text>
        <text x={(invX + batX) / 2} y={midY - 14} textAnchor="middle" dominantBaseline="middle"
          fill={batActive ? batColor : "#475569"} fontSize={10} fontWeight="600"
          fontFamily="Inter, system-ui, sans-serif">
          {fmt(batteryPower)}
        </text>

        {/* Phase / voltage below AC link */}
        {phaseStr && (
          <text x={(gridX + invX) / 2} y={midY + 14} textAnchor="middle" dominantBaseline="middle"
            fill="#64748b" fontSize={8} fontFamily="Inter, system-ui, sans-serif">
            {phaseStr}
          </text>
        )}

        <Node cx={gridX} cy={midY} icon="⚡" label="NET"       color="#f59e0b" />
        <Node cx={invX}  cy={midY} icon="🔄" label="OMVORMER"  color="#3b82f6" />
        <Node cx={batX}  cy={midY} icon="🔋" label="BATTERIJ"  color="#22c55e" />
      </svg>
    </div>
  );
}
