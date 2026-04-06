/**
 * EnergyMap – isometric 3D energy flow visualization
 *
 * Nodes: Solar (multi-string), Grid, House, Battery (multi-unit), EV (optional)
 * All node icons are hand-crafted SVG — no emoji.
 */

import { useState, useEffect, useCallback } from "react";
import { loadFlowCfg } from "./FlowSourcesSettings.jsx";

// ── Colors ────────────────────────────────────────────────────────────────────
const C = {
  house:   { t: "rgba(0,229,255,.07)",  s1: "rgba(0,175,215,.15)", s2: "rgba(0,110,170,.19)", b: "#00e5ff", glow: "#00e5ff" },
  solar:   { t: "rgba(255,200,0,.07)",  s1: "rgba(200,148,0,.15)", s2: "rgba(145,100,0,.19)", b: "#ffd600", glow: "#ffd600" },
  grid:    { t: "rgba(224,50,252,.07)", s1: "rgba(175,38,200,.15)", s2: "rgba(128,28,148,.19)", b: "#e040fb", glow: "#e040fb" },
  battery: { t: "rgba(0,230,100,.07)",  s1: "rgba(0,178,76,.15)",  s2: "rgba(0,128,56,.19)",  b: "#00e676", glow: "#00e676" },
  ev:      { t: "rgba(64,132,255,.07)", s1: "rgba(48,100,205,.15)", s2: "rgba(32,68,160,.19)", b: "#4488ff", glow: "#4488ff" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(w) {
  if (w == null) return "—";
  const abs = Math.abs(w);
  if (abs >= 1000) return `${(w / 1000).toFixed(2)} kW`;
  return `${Math.round(w)} W`;
}
function pct(v) { return v == null ? null : `${v.toFixed(0)}%`; }
function flowSpeed(power) {
  const abs = Math.abs(power ?? 0);
  if (abs < 50)   return "2.5s";
  if (abs < 500)  return "1.8s";
  if (abs < 2000) return "1.2s";
  return "0.7s";
}

// ── Data resolution ───────────────────────────────────────────────────────────
function resolveOne(sc, batteries, hwData, haData) {
  if (sc.source === "esphome") {
    const b = batteries.find((x) => x.id === sc.device_id);
    const v = b?.[sc.sensor];
    return v == null ? null : sc.invert ? -v : v;
  }
  if (sc.source === "homewizard") {
    const dev = hwData?.devices?.find((d) => d.id === sc.device_id);
    const s = dev?.sensors?.[sc.sensor];
    return s?.value == null ? null : sc.invert ? -s.value : s.value;
  }
  if (sc.source === "homeassistant") {
    const e = haData?.[sc.sensor];
    return e?.value == null ? null : sc.invert ? -e.value : e.value;
  }
  return null;
}
function resolveSlot(key, cfg, batteries, hwData, haData) {
  let sc = cfg?.[key];
  if (!sc) return null;
  if (!Array.isArray(sc)) sc = [sc];
  const isAvg = key === "bat_soc";
  let total = null, count = 0;
  for (const s of sc) {
    const v = resolveOne(s, batteries, hwData, haData);
    if (v != null) { total = (total ?? 0) + v; count++; }
  }
  if (total == null) return null;
  return isAvg && count > 0 ? total / count : total;
}

// ── Icon: Solar Panel ─────────────────────────────────────────────────────────
function SolarIcon({ cx, cy, hw }) {
  const W = hw * 1.02, H = hw * 0.40;
  const x = cx - W / 2, y = cy - H / 2 - hw * 0.04;
  const cols = Math.max(3, Math.round(W / 14));
  const cw = W / cols, ch = H / 2;
  return (
    <g>
      {/* Aluminium frame */}
      <rect x={x - 1.2} y={y - 1.2} width={W + 2.4} height={H + 2.4}
        fill="#374151" stroke="#6b7280" strokeWidth="0.7" rx="1.5" />
      {/* Panel body - dark navy blue */}
      <rect x={x} y={y} width={W} height={H}
        fill="#0c1a3f" stroke="#3b82f6" strokeWidth="0.8" rx="1" />
      {/* Vertical cell dividers */}
      {Array.from({ length: cols - 1 }, (_, i) => (
        <line key={`v${i}`}
          x1={x + (i + 1) * cw} y1={y}
          x2={x + (i + 1) * cw} y2={y + H}
          stroke="#2563eb" strokeWidth="0.65" />
      ))}
      {/* Horizontal divider */}
      <line x1={x} y1={y + H / 2} x2={x + W} y2={y + H / 2}
        stroke="#2563eb" strokeWidth="0.65" />
      {/* Cell fills */}
      {Array.from({ length: cols * 2 }, (_, k) => {
        const c = k % cols, r = Math.floor(k / cols);
        return (
          <rect key={k}
            x={x + c * cw + 0.9} y={y + r * ch + 0.9}
            width={cw - 1.8} height={ch - 1.8}
            fill={r === 0 ? "rgba(59,130,246,0.65)" : "rgba(37,99,235,0.50)"}
            rx="0.3" />
        );
      })}
      {/* Bus-bars */}
      {Array.from({ length: cols }, (_, c) => (
        <line key={`b${c}`}
          x1={x + c * cw + cw * 0.5} y1={y + 1}
          x2={x + c * cw + cw * 0.5} y2={y + H - 1}
          stroke="rgba(186,230,253,0.22)" strokeWidth="0.5" />
      ))}
      {/* Glare streak */}
      <line x1={x + W * 0.08} y1={y + H * 0.14}
        x2={x + W * 0.24} y2={y + H * 0.82}
        stroke="rgba(255,255,255,0.45)" strokeWidth="1.2" strokeLinecap="round" />
      {/* Glow border */}
      <rect x={x} y={y} width={W} height={H}
        fill="none" stroke="#93c5fd" strokeWidth="0.6" rx="1" opacity="0.6" />
    </g>
  );
}

// ── Icon: Battery with SOC fill ───────────────────────────────────────────────
function BatteryIcon({ cx, cy, hw, soc }) {
  const W = hw * 0.56, H = hw * 0.50;
  const x = cx - W / 2, y = cy - H / 2 - hw * 0.04;
  const tW = W * 0.34, tH = Math.max(2.5, H * 0.10);
  const fp  = Math.max(0, Math.min(100, soc ?? 50)) / 100;
  const fc  = soc == null ? "#38bdf8" : soc < 20 ? "#f87171" : soc < 50 ? "#fbbf24" : "#4ade80";
  const fh  = (H - 2.5) * fp;
  return (
    <g>
      {/* Positive terminal */}
      <rect x={cx - tW / 2} y={y - tH} width={tW} height={tH}
        fill="#cbd5e1" stroke="#94a3b8" strokeWidth="0.6" rx="1" />
      {/* Casing */}
      <rect x={x} y={y} width={W} height={H}
        fill="#1e2d3d" stroke="#475569" strokeWidth="1.0" rx="2.5" />
      {/* Fill level */}
      {fh > 0.5 && (
        <rect x={x + 1.8} y={y + H - 1.8 - fh} width={W - 3.6} height={fh}
          fill={fc} opacity="0.88" rx="1.5" />
      )}
      {/* Tick marks */}
      {[0.25, 0.5, 0.75].map((p) => (
        <line key={p}
          x1={x + 2} y1={y + H * (1 - p)}
          x2={x + W - 2} y2={y + H * (1 - p)}
          stroke="#1e293b" strokeWidth="0.8" opacity="0.9" />
      ))}
      {/* Sheen */}
      <rect x={x + W * 0.62} y={y + 1.5} width={W * 0.24} height={H - 3}
        fill="rgba(255,255,255,0.04)" rx="1" />
      {/* Outer glow border */}
      <rect x={x} y={y} width={W} height={H}
        fill="none" stroke="#64748b" strokeWidth="0.5" rx="2.5" />
    </g>
  );
}

// ── Icon: House ───────────────────────────────────────────────────────────────
function HouseIcon({ cx, cy, hw }) {
  const sc  = hw * 0.50;
  const bw  = sc * 1.18, bh = sc * 0.76;
  const bx  = cx - bw / 2;
  const by  = cy - bh / 2 + sc * 0.06;
  const rh  = sc * 0.54;
  const wW  = bw * 0.22, wH = bh * 0.30;
  const dW  = bw * 0.21, dH = bh * 0.50;
  return (
    <g>
      {/* Roof */}
      <polygon
        points={`${cx},${by - rh} ${bx - sc * 0.06},${by + 1.5} ${bx + bw + sc * 0.06},${by + 1.5}`}
        fill="#5b21b6" stroke="#8b5cf6" strokeWidth="1.0" />
      {/* Roof ridge */}
      <line x1={cx - 1} y1={by - rh + 1} x2={cx + 1} y2={by - rh + 1}
        stroke="#c4b5fd" strokeWidth="1.3" strokeLinecap="round" />
      {/* Chimney */}
      <rect x={cx + bw * 0.18} y={by - rh * 0.82}
        width={sc * 0.11} height={rh * 0.42}
        fill="#4c1d95" stroke="#7c3aed" strokeWidth="0.5" />
      <rect x={cx + bw * 0.165} y={by - rh * 0.82}
        width={sc * 0.14} height={1.8}
        fill="#7c3aed" rx="0.5" />
      {/* Walls */}
      <rect x={bx} y={by} width={bw} height={bh}
        fill="#2e2060" stroke="#6d28d9" strokeWidth="0.85" />
      {/* Wall shading */}
      <rect x={bx} y={by} width={bw * 0.5} height={bh}
        fill="rgba(255,255,255,0.03)" />
      {/* Left window — glowing cyan */}
      <rect x={bx + bw * 0.09} y={by + bh * 0.1} width={wW} height={wH}
        fill="rgba(0,229,255,0.35)" stroke="#00e5ff" strokeWidth="0.7" rx="0.5" />
      <line x1={bx + bw * 0.09 + wW / 2} y1={by + bh * 0.1}
        x2={bx + bw * 0.09 + wW / 2} y2={by + bh * 0.1 + wH}
        stroke="#67e8f9" strokeWidth="0.5" opacity="0.7" />
      <line x1={bx + bw * 0.09} y1={by + bh * 0.1 + wH / 2}
        x2={bx + bw * 0.09 + wW} y2={by + bh * 0.1 + wH / 2}
        stroke="#67e8f9" strokeWidth="0.5" opacity="0.7" />
      {/* Right window */}
      <rect x={bx + bw * 0.68} y={by + bh * 0.1} width={wW} height={wH}
        fill="rgba(0,229,255,0.35)" stroke="#00e5ff" strokeWidth="0.7" rx="0.5" />
      <line x1={bx + bw * 0.68 + wW / 2} y1={by + bh * 0.1}
        x2={bx + bw * 0.68 + wW / 2} y2={by + bh * 0.1 + wH}
        stroke="#67e8f9" strokeWidth="0.5" opacity="0.7" />
      <line x1={bx + bw * 0.68} y1={by + bh * 0.1 + wH / 2}
        x2={bx + bw * 0.68 + wW} y2={by + bh * 0.1 + wH / 2}
        stroke="#67e8f9" strokeWidth="0.5" opacity="0.7" />
      {/* Door */}
      <rect x={cx - dW / 2} y={by + bh - dH} width={dW} height={dH}
        fill="#150d35" stroke="#4f46e5" strokeWidth="0.75" rx="0.8" />
      <path d={`M ${cx - dW / 2},${by + bh - dH} Q ${cx},${by + bh - dH - dW * 0.35} ${cx + dW / 2},${by + bh - dH}`}
        fill="none" stroke="#4f46e5" strokeWidth="0.75" />
      <circle cx={cx + dW * 0.28} cy={by + bh - dH * 0.42} r="1.0" fill="#818cf8" />
    </g>
  );
}

// ── Icon: Electricity Pylon / Grid ────────────────────────────────────────────
function GridIcon({ cx, cy, hw }) {
  const sc  = Math.max(hw * 0.74, 20);
  const th  = sc * 0.92;
  const tw  = sc * 0.16, bw_ = sc * 0.40;
  const ty  = cy - th / 2, bot = cy + th / 2;
  const a1y = ty + th * 0.22;        // top cross-arm y
  const a2y = ty + th * 0.50;        // mid cross-arm y
  const a1w = sc * 0.54, a2w = sc * 0.38;
  return (
    <g>
      {/* Centre mast */}
      <line x1={cx} y1={ty} x2={cx} y2={bot}
        stroke="#c026d3" strokeWidth="1.6" strokeLinecap="round" />
      {/* Slanted legs */}
      <line x1={cx - tw} y1={ty + th * 0.11} x2={cx - bw_} y2={bot}
        stroke="#a21caf" strokeWidth="1.2" strokeLinecap="round" />
      <line x1={cx + tw} y1={ty + th * 0.11} x2={cx + bw_} y2={bot}
        stroke="#a21caf" strokeWidth="1.2" strokeLinecap="round" />
      {/* Base spreader */}
      <line x1={cx - bw_} y1={bot} x2={cx + bw_} y2={bot}
        stroke="#86198f" strokeWidth="1.1" strokeLinecap="round" />
      {/* Top cross-arm */}
      <line x1={cx - a1w} y1={a1y} x2={cx + a1w} y2={a1y}
        stroke="#a21caf" strokeWidth="1.3" strokeLinecap="round" />
      {/* Diagonal braces to top arm */}
      <line x1={cx} y1={ty + th * 0.04} x2={cx - a1w} y2={a1y}
        stroke="#c026d3" strokeWidth="0.65" opacity="0.6" />
      <line x1={cx} y1={ty + th * 0.04} x2={cx + a1w} y2={a1y}
        stroke="#c026d3" strokeWidth="0.65" opacity="0.6" />
      {/* Mid cross-arm */}
      <line x1={cx - a2w} y1={a2y} x2={cx + a2w} y2={a2y}
        stroke="#86198f" strokeWidth="1.05" strokeLinecap="round" />
      {/* Diagonal braces to mid arm */}
      <line x1={cx} y1={a1y + (a2y - a1y) * 0.15} x2={cx - a2w} y2={a2y}
        stroke="#a21caf" strokeWidth="0.55" opacity="0.5" />
      <line x1={cx} y1={a1y + (a2y - a1y) * 0.15} x2={cx + a2w} y2={a2y}
        stroke="#a21caf" strokeWidth="0.55" opacity="0.5" />
      {/* Insulator discs on top arm */}
      {[-1, -0.42, 0.42, 1].map((f, i) => (
        <circle key={i}
          cx={cx + f * a1w} cy={a1y + 2.8}
          r="1.5" fill="#e879f9" opacity="0.9" />
      ))}
      {/* Hanging wire catenary approximation */}
      {[-1, -0.42, 0.42, 1].map((f, i) => {
        const wx = cx + f * a1w;
        const nx = cx + (f < 0 ? f + 0.58 : f - 0.58) * a1w;
        const my = a1y + 5;
        return (
          <path key={`w${i}`}
            d={`M ${wx},${a1y + 4} Q ${(wx + nx) / 2},${my} ${nx},${a1y + 4}`}
            fill="none" stroke="#c026d3" strokeWidth="0.5" opacity="0.4" />
        );
      })}
      {/* Lightning bolt */}
      <polygon
        points={`
          ${cx - 2.5},${ty + th * 0.27}
          ${cx + 3.5},${ty + th * 0.27}
          ${cx + 0.2},${ty + th * 0.46}
          ${cx + 4.2},${ty + th * 0.46}
          ${cx - 2.8},${ty + th * 0.69}
          ${cx + 0.8},${ty + th * 0.50}
          ${cx - 3.2},${ty + th * 0.50}
        `}
        fill="#f0abfc" opacity="0.85" />
    </g>
  );
}

// ── Icon: Electric Vehicle ────────────────────────────────────────────────────
function EVIcon({ cx, cy, hw }) {
  const W  = hw * 0.90, H = hw * 0.44;
  const x  = cx - W / 2, y = cy - H / 2 - H * 0.07;
  const wr = Math.max(H * 0.34, 5);
  const wy = y + H + wr * 0.28;
  return (
    <g>
      {/* Lower body */}
      <rect x={x} y={y + H * 0.29} width={W} height={H * 0.73}
        fill="#152244" stroke="#4488ff" strokeWidth="0.85" rx="2.5" />
      {/* Cabin / upper body */}
      <path d={`M ${x + W * 0.17},${y + H * 0.31}
                L ${x + W * 0.27},${y}
                L ${x + W * 0.79},${y}
                L ${x + W * 0.88},${y + H * 0.31} Z`}
        fill="#0e1a36" stroke="#4488ff" strokeWidth="0.85" />
      {/* Windshield */}
      <path d={`M ${x + W * 0.30},${y + H * 0.28}
                L ${x + W * 0.37},${y + H * 0.06}
                L ${x + W * 0.67},${y + H * 0.06}
                L ${x + W * 0.75},${y + H * 0.28} Z`}
        fill="rgba(64,132,255,0.22)" stroke="#4488ff" strokeWidth="0.4" />
      {/* Rear quarter window */}
      <path d={`M ${x + W * 0.76},${y + H * 0.27}
                L ${x + W * 0.81},${y + H * 0.07}
                L ${x + W * 0.88},${y + H * 0.22} Z`}
        fill="rgba(64,132,255,0.15)" stroke="#4488ff" strokeWidth="0.4" />
      {/* Front headlight */}
      <rect x={x + W * 0.01} y={y + H * 0.48} width={W * 0.08} height={H * 0.19}
        fill="#93c5fd" rx="1.5" opacity="0.9" />
      <rect x={x + W * 0.02} y={y + H * 0.49} width={W * 0.05} height={H * 0.09}
        fill="white" rx="1" opacity="0.5" />
      {/* Rear light */}
      <rect x={x + W * 0.93} y={y + H * 0.46} width={W * 0.05} height={H * 0.23}
        fill="#ef4444" rx="0.8" opacity="0.88" />
      {/* Door separation line */}
      <line x1={x + W * 0.49} y1={y + H * 0.31}
        x2={x + W * 0.49} y2={y + H * 0.98}
        stroke="#1d3a7a" strokeWidth="0.5" />
      {/* Side stripe */}
      <line x1={x + W * 0.1} y1={y + H * 0.59}
        x2={x + W * 0.9} y2={y + H * 0.59}
        stroke="#4488ff" strokeWidth="0.4" opacity="0.35" />
      {/* Wheels */}
      {[0.21, 0.78].map((fx, i) => (
        <g key={i}>
          <circle cx={x + W * fx} cy={wy} r={wr}
            fill="#080f1e" stroke="#4b5563" strokeWidth="0.85" />
          <circle cx={x + W * fx} cy={wy} r={wr * 0.58}
            fill="#1a2540" stroke="#374151" strokeWidth="0.5" />
          {/* Wheel spokes */}
          {[0, 60, 120].map((deg) => {
            const rad = (deg * Math.PI) / 180;
            return (
              <line key={deg}
                x1={x + W * fx + Math.cos(rad) * wr * 0.2}
                y1={wy + Math.sin(rad) * wr * 0.2}
                x2={x + W * fx + Math.cos(rad) * wr * 0.52}
                y2={wy + Math.sin(rad) * wr * 0.52}
                stroke="#475569" strokeWidth="0.6" />
            );
          })}
          <circle cx={x + W * fx} cy={wy} r={wr * 0.18} fill="#475569" />
        </g>
      ))}
      {/* Charging port */}
      <rect x={x + W * 0.875} y={y + H * 0.34} width={W * 0.095} height={H * 0.22}
        fill="#4488ff" opacity="0.68" rx="1.5" stroke="#60a5fa" strokeWidth="0.4" />
      {/* Charging plug symbol */}
      <circle cx={x + W * 0.92} cy={y + H * 0.44} r="1.2"
        fill="#93c5fd" opacity="0.9" />
    </g>
  );
}

// ── IsoNode – flat diamond tile with icon (no cube sides) ────────────────────
function IsoNode({ cx, cy, hw, nc, iconEl, label, val, valColor, sub }) {
  const s   = cy + hw * 0.5;   // south point
  const top = `${cx},${cy - hw * 0.5} ${cx + hw},${cy} ${cx},${s} ${cx - hw},${cy}`;
  return (
    <g>
      {/* Diamond top face only */}
      <polygon points={top} fill={nc.t} stroke={nc.b} strokeWidth={1.8} filter="url(#em-glow)" />
      {/* Icon */}
      {iconEl && <g filter="url(#em-glow-soft)">{iconEl}</g>}
      {/* Value pill */}
      {val != null && (
        <g>
          <rect x={cx - 24} y={cy + hw * 0.22} width={48} height={12}
            fill="rgba(0,0,0,0.62)" rx="5" />
          <text x={cx} y={cy + hw * 0.22 + 6}
            textAnchor="middle" dominantBaseline="middle"
            fill={valColor || nc.b} fontSize={8} fontWeight="700"
            fontFamily="'Courier New',Courier,monospace">{val}</text>
        </g>
      )}
      {/* Label below diamond */}
      <text x={cx} y={s + 13}
        textAnchor="middle" dominantBaseline="middle"
        fill="var(--text-muted)" fontSize={8.5} letterSpacing="0.6"
        fontFamily="Inter,system-ui,sans-serif">{label}</text>
      {sub && (
        <text x={cx} y={s + 24}
          textAnchor="middle" dominantBaseline="middle"
          fill="var(--text-dim)" fontSize={7.5}
          fontFamily="'Courier New',Courier,monospace">{sub}</text>
      )}
    </g>
  );
}

// ── FlowLine – animated neon glow connection ──────────────────────────────────
function FlowLine({ x1, y1, x2, y2, color, active, reverse, power, labelText }) {
  if (!active) {
    return (
      <line x1={x1} y1={y1} x2={x2} y2={y2}
        stroke="var(--border)" strokeWidth={2.5} strokeLinecap="round" />
    );
  }
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy);
  const ux = dx / len, uy = dy / len;
  const as = 6;
  const px = -uy * as * 0.55, py = ux * as * 0.55;
  const dur = flowSpeed(power);
  const mx = (x1 + x2) / 2 - uy * 14, my = (y1 + y2) / 2 + ux * 14;
  return (
    <g>
      {/* Shadow track */}
      <line x1={x1} y1={y1} x2={x2} y2={y2}
        stroke="var(--bg-card)" strokeWidth={4} strokeLinecap="round" />
      <g filter="url(#em-glow)">
        {/* Animated dashes */}
        <line x1={x1} y1={y1} x2={x2} y2={y2}
          stroke={color} strokeWidth={2.5} strokeDasharray="8 6"
          strokeLinecap="round" opacity={0.9}>
          <animate attributeName="stroke-dashoffset"
            from={reverse ? "0" : "56"} to={reverse ? "56" : "0"}
            dur={dur} repeatCount="indefinite" />
        </line>
        {/* Arrowhead */}
        <polygon
          points={`${x2},${y2} ${x2 - ux * as + px},${y2 - uy * as + py} ${x2 - ux * as - px},${y2 - uy * as - py}`}
          fill={color} opacity={0.9} />
      </g>
      {labelText && (
        <text x={mx} y={my} textAnchor="middle" dominantBaseline="middle"
          fill={color} fontSize={8.5} fontWeight="700"
          fontFamily="'Courier New',Courier,monospace">{labelText}</text>
      )}
    </g>
  );
}

// ── Layout helpers ────────────────────────────────────────────────────────────
function batPositions(n) {
  if (n <= 0) n = 1;
  if (n === 1) return [{ cx: 160, cy: 355, hw: 62 }];
  if (n === 2) return [
    { cx: 106, cy: 355, hw: 50 },
    { cx: 214, cy: 355, hw: 50 },
  ];
  if (n === 3) return [
    { cx: 80,  cy: 355, hw: 42 },
    { cx: 160, cy: 355, hw: 42 },
    { cx: 240, cy: 355, hw: 42 },
  ];
  return [
    { cx: 64,  cy: 355, hw: 36 },
    { cx: 136, cy: 355, hw: 36 },
    { cx: 208, cy: 355, hw: 36 },
    { cx: 280, cy: 355, hw: 36 },
  ];
}

function solarPositions(n) {
  if (n <= 1) return [{ cx: 165, cy: 118, hw: 72 }];
  if (n === 2) return [
    { cx: 112, cy: 116, hw: 58 },
    { cx: 220, cy: 116, hw: 58 },
  ];
  return [
    { cx: 88,  cy: 114, hw: 48 },
    { cx: 165, cy: 114, hw: 48 },
    { cx: 242, cy: 114, hw: 48 },
  ];
}

function clusterCenter(positions) {
  const avgX = positions.reduce((s, p) => s + p.cx, 0) / positions.length;
  const avgY = positions.reduce((s, p) => s + p.cy, 0) / positions.length;
  return { cx: avgX, cy: avgY };
}

// ── Main component ────────────────────────────────────────────────────────────
export default function EnergyMap({ batteries = [], phaseVoltages, acVoltage }) {
  const [hwData, setHwData] = useState(null);
  const [haData, setHaData] = useState({});
  const [cfg,    setCfg]    = useState(() => loadFlowCfg());

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
      const r = await fetch("api/homewizard/data");
      if (r.ok) setHwData(await r.json());
    } catch {}
  }, []);

  const pollHa = useCallback(async (currentCfg) => {
    const ids = Object.values(currentCfg).flat()
      .filter((sc) => sc?.source === "homeassistant" && sc.sensor)
      .map((sc) => sc.sensor);
    if (!ids.length) return;
    try {
      const r = await fetch("api/ha/poll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_ids: ids }),
      });
      if (r.ok) setHaData(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    pollHw(); pollHa(cfg);
    const id = setInterval(() => { pollHw(); pollHa(cfg); }, 10000);
    return () => clearInterval(id);
  }, [pollHw, pollHa, cfg]);

  // ── ESPHome aggregates ────────────────────────────────────────────────────
  let totalAc = null, totalBat = null;
  for (const b of batteries) {
    if (b.acPower  != null) totalAc  = (totalAc  ?? 0) + b.acPower;
    if (b.batPower != null) totalBat = (totalBat ?? 0) + b.batPower;
  }
  const socsWithData = batteries.map((b) => b.soc).filter((v) => v != null);
  const avgSoc = socsWithData.length > 0
    ? socsWithData.reduce((a, v) => a + v, 0) / socsWithData.length : null;

  // ── Slot resolution ───────────────────────────────────────────────────────
  const solarPower  = resolveSlot("solar_power", cfg, batteries, hwData, haData);
  const netPowerRaw = resolveSlot("net_power",   cfg, batteries, hwData, haData);
  const batPowerRaw = resolveSlot("bat_power",   cfg, batteries, hwData, haData);
  const batSoc      = resolveSlot("bat_soc",     cfg, batteries, hwData, haData) ?? avgSoc;
  const evPower     = resolveSlot("ev_power",    cfg, batteries, hwData, haData);

  // netDisplayPower: positive = export to grid
  const netDisplayPower = netPowerRaw != null ? -netPowerRaw : totalAc;
  const batDisplayPower = batPowerRaw ?? totalBat;

  const housePower = (netDisplayPower != null || batDisplayPower != null || solarPower != null)
    ? (batDisplayPower ?? 0) - (netDisplayPower ?? 0)
      + (solarPower ?? 0) - (evPower ?? 0)
    : null;

  // ── Layout ────────────────────────────────────────────────────────────────
  const W = 800, H = 500;

  const numBat   = Math.min(Math.max(batteries.length, 1), 4);
  const numSolar = Math.max(
    Array.isArray(cfg.solar_power) ? cfg.solar_power.length : (cfg.solar_power ? 1 : 0), 0
  );
  const showSolar = numSolar > 0 || solarPower != null;
  const showEv    = Array.isArray(cfg.ev_power) ? cfg.ev_power.length > 0 : !!cfg.ev_power;

  const batPos  = batPositions(numBat);
  const solPos  = solarPositions(Math.max(numSolar, 1));
  const batCC   = clusterCenter(batPos);
  const solCC   = clusterCenter(solPos);

  const HOUSE = { cx: 400, cy: 200, hw: 80 };
  const GRID  = { cx: 628, cy: 122, hw: 50 };
  const EV    = { cx: 632, cy: 350, hw: 64 };

  const hCx = HOUSE.cx, hCy = HOUSE.cy;
  const gCx = GRID.cx,  gCy = GRID.cy;
  const bCx = batCC.cx, bCy = batCC.cy;
  const sCx = solCC.cx, sCy = solCC.cy;
  const eCx = EV.cx,    eCy = EV.cy;

  // ── Flow colors & directions ──────────────────────────────────────────────
  const netActive = netDisplayPower != null && Math.abs(netDisplayPower) > 5;
  const netToGrid = (netDisplayPower ?? 0) > 0;
  const netColor  = netActive ? (netToGrid ? "#22c55e" : "#ef4444") : "var(--border)";

  const batActive = batDisplayPower != null && Math.abs(batDisplayPower) > 5;
  const batDisch  = (batDisplayPower ?? 0) > 0;
  const batColor  = batActive ? (batDisch ? "#f59e0b" : "#3b82f6") : "var(--border)";

  const solarActive = solarPower != null && solarPower > 10;
  const evActive    = evPower != null && evPower > 10;

  const socColor = batSoc == null ? "#475569"
    : batSoc < 20 ? "#ef4444" : batSoc < 50 ? "#f59e0b" : "#22c55e";

  return (
    <div className="energy-map-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="energy-map-svg"
        aria-label="Energie stroomoverzicht">
        <defs>
          {/* Neon glow filter */}
          <filter id="em-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="3.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          {/* Soft icon glow (less spread) */}
          <filter id="em-glow-soft" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="1.8" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          {/* Isometric floor grid */}
          <pattern id="iso-grid" x="0" y="0" width="52" height="30"
            patternUnits="userSpaceOnUse">
            <line x1="26" y1="0"  x2="52" y2="15" stroke="rgba(0,180,255,0.08)" strokeWidth="0.6" />
            <line x1="0"  y1="15" x2="26" y2="0"  stroke="rgba(0,180,255,0.08)" strokeWidth="0.6" />
            <line x1="0"  y1="15" x2="26" y2="30" stroke="rgba(0,180,255,0.08)" strokeWidth="0.6" />
            <line x1="26" y1="30" x2="52" y2="15" stroke="rgba(0,180,255,0.08)" strokeWidth="0.6" />
          </pattern>
          {/* Edge vignette */}
          <radialGradient id="edge-fade" cx="50%" cy="50%" r="70%">
            <stop offset="60%"  stopColor="#0a0e1a" stopOpacity="0" />
            <stop offset="100%" stopColor="#0a0e1a" stopOpacity="0.85" />
          </radialGradient>
        </defs>

        {/* Background */}
        <rect width={W} height={H} fill="var(--bg-card)" />
        <rect width={W} height={H} fill="url(#iso-grid)" />

        {/* ── Flow lines (behind nodes) ── */}
        <FlowLine x1={gCx} y1={gCy} x2={hCx} y2={hCy}
          color={netColor} active={netActive} reverse={netToGrid} power={netDisplayPower}
          labelText={netActive ? fmt(netPowerRaw ?? -netDisplayPower) : null} />

        <FlowLine x1={bCx} y1={bCy} x2={hCx} y2={hCy}
          color={batColor} active={batActive} reverse={!batDisch} power={batDisplayPower}
          labelText={batActive ? fmt(batDisplayPower) : null} />

        {showSolar && (
          <FlowLine x1={sCx} y1={sCy} x2={hCx} y2={hCy}
            color={C.solar.glow} active={solarActive} reverse={false} power={solarPower}
            labelText={solarActive ? fmt(solarPower) : null} />
        )}

        {showEv && (
          <FlowLine x1={hCx} y1={hCy} x2={eCx} y2={eCy}
            color={C.ev.glow} active={evActive} reverse={false} power={evPower}
            labelText={evActive ? fmt(evPower) : null} />
        )}

        {/* ── Nodes (on top of lines) ── */}

        {/* Solar panels – each string resolves its own sensor */}
        {showSolar && (() => {
          const solCfgs = Array.isArray(cfg.solar_power)
            ? cfg.solar_power
            : cfg.solar_power ? [cfg.solar_power] : [];
          return solPos.map((p, i) => {
            const sc = solCfgs[i];
            const strPwr = sc ? resolveOne(sc, batteries, hwData, haData) : null;
            return (
              <IsoNode key={`sol-${i}`} {...p} nc={C.solar}
                iconEl={<SolarIcon cx={p.cx} cy={p.cy} hw={p.hw} />}
                label={numSolar > 1 ? `STRING ${i + 1}` : "ZONNEPANELEN"}
                val={strPwr != null ? fmt(strPwr) : null}
              />
            );
          });
        })()}
        {showSolar && numSolar > 1 && solarPower != null && (
          <text x={sCx} y={solPos[0].cy - solPos[0].hw * 0.5 - 10}
            textAnchor="middle" fill={C.solar.b} fontSize={9} fontWeight="700"
            fontFamily="'Courier New',Courier,monospace" filter="url(#em-glow)">
            {fmt(solarPower)} totaal
          </text>
        )}

        {/* Grid */}
        <IsoNode {...GRID} nc={C.grid}
          iconEl={<GridIcon cx={GRID.cx} cy={GRID.cy} hw={GRID.hw} />}
          label="ELEKTRICITEITSNET"
          val={fmt(netPowerRaw ?? (netDisplayPower != null ? -netDisplayPower : null))}
          valColor={netActive ? netColor : C.grid.b}
          sub={netActive ? (netToGrid ? "↑ teruglevering" : "↓ afname") : null}
        />

        {/* House */}
        <IsoNode {...HOUSE} nc={C.house}
          iconEl={<HouseIcon cx={HOUSE.cx} cy={HOUSE.cy} hw={HOUSE.hw} />}
          label="WONING"
          val={housePower != null ? fmt(housePower) : null}
          valColor="#a78bfa"
          sub={housePower != null ? "verbruik" : null}
        />

        {/* Batteries */}
        {batPos.map((p, i) => {
          const b = batteries[i];
          const thisSoc = b?.soc ?? batSoc;
          return (
            <IsoNode key={`bat-${i}`} {...p} nc={C.battery}
              iconEl={<BatteryIcon cx={p.cx} cy={p.cy} hw={p.hw} soc={thisSoc} />}
              label={b ? b.name.slice(0, 11) : "BATTERIJ"}
              val={batPos.length === 1
                ? fmt(batDisplayPower)
                : (b ? fmt(b.batPower ?? null) : null)}
              valColor={batActive ? batColor : C.battery.b}
              sub={thisSoc != null ? pct(thisSoc) : null}
            />
          );
        })}
        {batPos.length > 1 && batDisplayPower != null && (
          <text x={bCx} y={batPos[0].cy - batPos[0].hw * 0.5 - 10}
            textAnchor="middle" fill={batColor} fontSize={9} fontWeight="700"
            fontFamily="'Courier New',Courier,monospace" filter="url(#em-glow)">
            {fmt(batDisplayPower)} totaal
            {batSoc != null ? ` · ${batSoc.toFixed(0)}% gem.` : ""}
          </text>
        )}

        {/* EV */}
        {showEv && (
          <IsoNode {...EV} nc={C.ev}
            iconEl={<EVIcon cx={EV.cx} cy={EV.cy} hw={EV.hw} />}
            label="EV LADER"
            val={evPower != null ? fmt(evPower) : "—"}
            valColor={evActive ? C.ev.b : "var(--text-muted)"}
          />
        )}

        {/* Phase voltages label near grid–house line */}
        {(phaseVoltages || acVoltage) && (() => {
          const vStr = phaseVoltages
            ? [
                phaseVoltages.L1 != null ? `L1:${phaseVoltages.L1.toFixed(0)}V` : null,
                phaseVoltages.L2 != null ? `L2:${phaseVoltages.L2.toFixed(0)}V` : null,
                phaseVoltages.L3 != null ? `L3:${phaseVoltages.L3.toFixed(0)}V` : null,
              ].filter(Boolean).join("  ")
            : `${acVoltage.toFixed(1)} V`;
          const mx = (gCx + hCx) / 2 + 8, my = (gCy + hCy) / 2 + 14;
          return (
            <text x={mx} y={my} textAnchor="middle"
              fill="var(--text-muted)" fontSize={7.5}
              fontFamily="'Courier New',Courier,monospace">{vStr}</text>
          );
        })()}
      </svg>
    </div>
  );
}
