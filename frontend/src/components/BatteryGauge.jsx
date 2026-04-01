import React from "react";

/**
 * Circular SVG gauge for State of Charge.
 * Props: { soc, remaining, total }
 *  - soc: number 0-100 (percentage)
 *  - remaining: kWh remaining (optional)
 *  - total: total capacity kWh (optional)
 */
export default function BatteryGauge({ soc, remaining, total }) {
  const size = 160;
  const cx = size / 2;
  const cy = size / 2;
  const r = 62;
  const strokeWidth = 10;
  const circumference = 2 * Math.PI * r;
  const clampedSoc = Math.max(0, Math.min(100, soc ?? 0));
  const offset = circumference * (1 - clampedSoc / 100);

  const color =
    clampedSoc > 50
      ? "#22c55e"
      : clampedSoc > 20
      ? "#f59e0b"
      : "#ef4444";

  const displaySoc =
    soc == null ? "—" : `${Math.round(clampedSoc)}%`;

  const displayRemaining =
    remaining != null
      ? `${remaining.toFixed(1)} kWh`
      : total != null
      ? `— kWh`
      : null;

  return (
    <div className="battery-gauge">
      <svg
        className="battery-gauge-svg"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        aria-label={`Battery: ${displaySoc}`}
      >
        {/* Background track */}
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke="#1e293b"
          strokeWidth={strokeWidth}
        />
        {/* Outer ring decoration */}
        <circle
          cx={cx}
          cy={cy}
          r={r + strokeWidth / 2 + 4}
          fill="none"
          stroke="#0f172a"
          strokeWidth={2}
        />
        <circle
          cx={cx}
          cy={cy}
          r={r - strokeWidth / 2 - 4}
          fill="none"
          stroke="#0f172a"
          strokeWidth={2}
        />
        {/* Progress arc */}
        {soc != null && (
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${cx} ${cy})`}
            style={{ transition: "stroke-dashoffset 0.6s ease, stroke 0.4s ease" }}
          />
        )}
        {/* Glow */}
        {soc != null && (
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth + 4}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${cx} ${cy})`}
            opacity={0.15}
            style={{ transition: "stroke-dashoffset 0.6s ease" }}
          />
        )}
        {/* Center text – percentage */}
        <text
          x={cx}
          y={cy - (displayRemaining ? 8 : 4)}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={soc != null ? color : "#64748b"}
          fontSize={soc != null ? 28 : 22}
          fontWeight="700"
          fontFamily="Inter, system-ui, sans-serif"
        >
          {displaySoc}
        </text>
        {/* Remaining kWh */}
        {displayRemaining && (
          <text
            x={cx}
            y={cy + 20}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="#94a3b8"
            fontSize={11}
            fontFamily="Inter, system-ui, sans-serif"
          >
            {displayRemaining}
          </text>
        )}
        {/* SOC label */}
        <text
          x={cx}
          y={cy + (displayRemaining ? 37 : 22)}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#475569"
          fontSize={10}
          fontFamily="Inter, system-ui, sans-serif"
          letterSpacing="1"
        >
          STATE OF CHARGE
        </text>
      </svg>
    </div>
  );
}
