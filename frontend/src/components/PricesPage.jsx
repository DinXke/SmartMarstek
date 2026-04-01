import React, { useState, useEffect, useCallback } from "react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const total = (p) =>
  (p.marketPrice || 0) +
  (p.marketPriceTax || 0) +
  (p.sourcingMarkupPrice || 0) +
  (p.energyTaxPrice || 0);

const fmtCt = (v) => (v == null ? "—" : `${(v * 100).toFixed(2)} ct`);
const fmtEur = (v) => (v == null ? "—" : `€ ${v.toFixed(4)}`);

/** Return HH:MM from an ISO timestamp string */
const hhmm = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
};

/** Colour based on percentile within the day's prices */
function priceColor(value, min, max) {
  if (max === min) return "#3b82f6";
  const pct = (value - min) / (max - min);
  if (pct < 0.25) return "#22c55e";
  if (pct < 0.50) return "#84cc16";
  if (pct < 0.75) return "#f59e0b";
  return "#ef4444";
}

/** Detect resolution from price data: 15min if any slot is 15 minutes wide */
function detectResolution(prices) {
  if (!prices?.length) return "1h";
  const p = prices[0];
  const diffMs = new Date(p.till) - new Date(p.from);
  return diffMs <= 15 * 60 * 1000 ? "15min" : "1h";
}

/** Aggregate quarter-hour prices into hourly averages */
function aggregateToHourly(prices) {
  const order = [];
  const buckets = {};
  for (const p of prices) {
    const from = new Date(p.from);
    const key = `${from.getFullYear()}-${from.getMonth()}-${from.getDate()}-${from.getHours()}`;
    if (!buckets[key]) {
      order.push(key);
      buckets[key] = {
        from: p.from,
        till: p.till,
        marketPrice: p.marketPrice || 0,
        marketPriceTax: p.marketPriceTax || 0,
        sourcingMarkupPrice: p.sourcingMarkupPrice || 0,
        energyTaxPrice: p.energyTaxPrice || 0,
        _n: 1,
      };
    } else {
      const b = buckets[key];
      b.till = p.till;
      b.marketPrice        += p.marketPrice        || 0;
      b.marketPriceTax     += p.marketPriceTax     || 0;
      b.sourcingMarkupPrice += p.sourcingMarkupPrice || 0;
      b.energyTaxPrice     += p.energyTaxPrice     || 0;
      b._n++;
    }
  }
  return order.map((key) => {
    const b = buckets[key];
    return {
      from:                b.from,
      till:                b.till,
      marketPrice:         b.marketPrice        / b._n,
      marketPriceTax:      b.marketPriceTax     / b._n,
      sourcingMarkupPrice: b.sourcingMarkupPrice / b._n,
      energyTaxPrice:      b.energyTaxPrice     / b._n,
    };
  });
}

// localStorage keys
const RES_KEY    = "marstek_price_resolution";
const SOURCE_KEY = "marstek_price_source";

// ---------------------------------------------------------------------------
// Bar chart (SVG, pure, no library)
// ---------------------------------------------------------------------------

function PriceChart({ prices }) {
  const now = new Date();

  const totals = prices.map(total);
  const min    = Math.min(...totals);
  const max    = Math.max(...totals);
  const avg    = totals.reduce((a, b) => a + b, 0) / totals.length;

  const W = 780, H = 180, PAD_L = 44, PAD_R = 8, PAD_T = 16, PAD_B = 32;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;
  const barW   = chartW / prices.length;
  const yScale = chartH / (max * 1.08 || 1);

  const yTicks = [0, max * 0.25, max * 0.5, max * 0.75, max];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: "100%", height: "auto", display: "block" }}
      aria-label="Electricity prices"
    >
      {/* Y grid lines */}
      {yTicks.map((t) => {
        const y = PAD_T + chartH - t * yScale;
        return (
          <g key={t}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y} y2={y}
              stroke="#334155" strokeWidth="0.5" strokeDasharray="3 3" />
            <text x={PAD_L - 4} y={y + 4} textAnchor="end"
              fill="#64748b" fontSize="9">
              {(t * 100).toFixed(0)}
            </text>
          </g>
        );
      })}

      {/* Average line */}
      {(() => {
        const y = PAD_T + chartH - avg * yScale;
        return (
          <line x1={PAD_L} x2={W - PAD_R} y1={y} y2={y}
            stroke="#64748b" strokeWidth="1" strokeDasharray="6 3" />
        );
      })()}

      {/* Bars */}
      {prices.map((p, i) => {
        const t     = total(p);
        const bh    = t * yScale;
        const x     = PAD_L + i * barW;
        const y     = PAD_T + chartH - bh;
        const color = priceColor(t, min, max);

        const from = new Date(p.from);
        const till = new Date(p.till);
        const isCurrent = now >= from && now < till;

        // Show X-axis label every 3 hours, on the :00 slot only
        const hour = from.getHours();
        const mins = from.getMinutes();
        const showLabel = hour % 3 === 0 && mins === 0;

        return (
          <g key={i}>
            {isCurrent && (
              <rect x={x} y={PAD_T} width={barW} height={chartH}
                fill="rgba(255,255,255,0.06)" rx="2" />
            )}
            <rect
              x={x + 0.5} y={y} width={Math.max(barW - 1, 0.5)} height={bh}
              fill={color}
              opacity={isCurrent ? 1 : 0.75}
              rx="1"
            >
              <title>{hhmm(p.from)} – {hhmm(p.till)}: {fmtCt(t)}</title>
            </rect>
            {isCurrent && (
              <rect x={x + 0.5} y={y - 2} width={Math.max(barW - 1, 0.5)} height={2}
                fill="#fff" rx="1" />
            )}
            {showLabel && (
              <text x={x + barW / 2} y={H - 4} textAnchor="middle"
                fill="#64748b" fontSize="9">
                {String(hour).padStart(2, "0")}h
              </text>
            )}
          </g>
        );
      })}

      {/* Y-axis label */}
      <text x={8} y={H / 2} textAnchor="middle" fill="#64748b" fontSize="9"
        transform={`rotate(-90, 8, ${H / 2})`}>
        ct/kWh
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Login form
// ---------------------------------------------------------------------------

function LoginPanel({ onLogin, status }) {
  const [email,    setEmail]    = useState("");
  const [password, setPassword] = useState("");
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState(null);

  const handleLogin = async () => {
    if (!email || !password) { setError("Vul email en wachtwoord in."); return; }
    setLoading(true); setError(null);
    try {
      const res = await fetch("/api/frank/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok || data.error) { setError(data.error || "Login mislukt."); return; }
      onLogin(data);
    } catch { setError("Netwerkfout."); }
    finally   { setLoading(false); }
  };

  const handleLogout = async () => {
    await fetch("/api/frank/logout", { method: "POST" });
    onLogin(null);
  };

  if (status?.loggedIn) {
    return (
      <div className="frank-login-bar logged-in">
        <span>✓ Ingelogd als <strong>{status.email}</strong> ({status.country ?? "NL"}) — persoonlijke tarieven actief</span>
        <button className="btn btn-ghost btn-sm" onClick={handleLogout}>Uitloggen</button>
      </div>
    );
  }

  return (
    <div className="frank-login-bar">
      <div className="frank-login-title">🔐 Frank Energie account (optioneel)</div>
      <div className="frank-login-desc">
        Log in voor persoonlijke tarieven. Zonder account worden publieke marktprijzen getoond.
        Werkt voor zowel Frank Energie NL als BE accounts.
      </div>
      {error && <div className="form-error">{error}</div>}
      <div className="frank-login-form">
        <input className="form-input" type="email"    placeholder="E-mailadres"
          value={email}    onChange={(e) => setEmail(e.target.value)} />
        <input className="form-input" type="password" placeholder="Wachtwoord"
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <button className="btn btn-primary" onClick={handleLogin} disabled={loading}>
          {loading ? "Bezig…" : "Inloggen"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Price table
// ---------------------------------------------------------------------------

function PriceTable({ prices, is15min }) {
  const now = new Date();
  const totals = prices.map(total);
  const min = Math.min(...totals);
  const max = Math.max(...totals);

  return (
    <div className="price-table-wrap">
      <table className="price-table">
        <thead>
          <tr>
            <th>{is15min ? "Kwartier" : "Uur"}</th>
            <th>Totaal</th>
            <th>Marktprijs</th>
            <th>Belasting</th>
            <th>Opslag</th>
            <th>Energiebelasting</th>
          </tr>
        </thead>
        <tbody>
          {prices.map((p, i) => {
            const t    = total(p);
            const from = new Date(p.from);
            const till = new Date(p.till);
            const isCur = now >= from && now < till;
            const color = priceColor(t, min, max);
            return (
              <tr key={i} className={isCur ? "price-row-current" : ""}>
                <td style={{ fontVariantNumeric: "tabular-nums" }}>
                  {hhmm(p.from)}–{hhmm(p.till)}
                </td>
                <td style={{ color, fontWeight: isCur ? 700 : 400 }}>{fmtCt(t)}</td>
                <td>{fmtCt(p.marketPrice)}</td>
                <td>{fmtCt(p.marketPriceTax)}</td>
                <td>{fmtCt(p.sourcingMarkupPrice)}</td>
                <td>{fmtCt(p.energyTaxPrice)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stats row
// ---------------------------------------------------------------------------

function PriceStats({ prices, is15min }) {
  const totals = prices.map(total);
  const min    = Math.min(...totals);
  const max    = Math.max(...totals);
  const avg    = totals.reduce((a, b) => a + b, 0) / totals.length;

  // For 15-min: show 6 cheapest/most expensive slots; for hourly: 4
  const n = is15min ? 6 : 4;

  const cheapSlots = prices
    .map((p, i) => ({ p, t: totals[i] }))
    .sort((a, b) => a.t - b.t)
    .slice(0, n)
    .map(({ p }) => hhmm(p.from))
    .join(", ");

  const expSlots = prices
    .map((p, i) => ({ p, t: totals[i] }))
    .sort((a, b) => b.t - a.t)
    .slice(0, n)
    .map(({ p }) => hhmm(p.from))
    .join(", ");

  const stats = [
    { label: "Laagste",   value: fmtCt(min), color: "#22c55e" },
    { label: "Gemiddeld", value: fmtCt(avg), color: "#94a3b8" },
    { label: "Hoogste",   value: fmtCt(max), color: "#ef4444" },
  ];

  const slotLabel = is15min ? "kwartieren" : "uren";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="price-stats">
        {stats.map((s) => (
          <div key={s.label} className="price-stat-card">
            <div className="price-stat-label">{s.label}</div>
            <div className="price-stat-value" style={{ color: s.color }}>{s.value}</div>
          </div>
        ))}
      </div>
      <div className="price-hint-row">
        <span className="price-hint cheap">🟢 Goedkoopste {slotLabel}: {cheapSlots}</span>
        <span className="price-hint exp">🔴 Duurste {slotLabel}: {expSlots}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main PricesPage
// ---------------------------------------------------------------------------

export default function PricesPage() {
  const [data,       setData]       = useState(null);
  const [status,     setStatus]     = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState(null);
  const [day,        setDay]        = useState("today");    // "today" | "tomorrow"
  const [view,       setView]       = useState("chart");    // "chart" | "table"
  const [resolution, setResolution] = useState(
    () => localStorage.getItem(RES_KEY) || "1h"             // "1h" | "15min"
  );
  const [source, setSource] = useState(
    () => localStorage.getItem(SOURCE_KEY) || "frank"       // "frank" | "entsoe"
  );

  const saveResolution = (r) => { setResolution(r); localStorage.setItem(RES_KEY, r); };
  const saveSource     = (s) => { setSource(s);     localStorage.setItem(SOURCE_KEY, s); };

  const loadStatus = useCallback(async () => {
    const r = await fetch("/api/frank/status");
    setStatus(await r.json());
  }, []);

  const loadPrices = useCallback(async (src) => {
    setLoading(true); setError(null);
    try {
      let url;
      if (src === "entsoe") {
        // Pass country from Frank session if available, else default BE
        const country = status?.country || "BE";
        url = `/api/prices/entsoe?country=${country}`;
      } else {
        url = "/api/prices/electricity";
      }
      const r = await fetch(url);
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const d = await r.json(); if (d.error) detail += `: ${d.error}`; } catch {}
        console.error("[PricesPage] prices fetch →", detail);
        throw new Error(detail);
      }
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      console.log("[PricesPage] prices loaded  source=%s  today=%d  tomorrow=%d",
        src, d.today?.length, d.tomorrow?.length);
      setData(d);
    } catch (e) {
      console.error("[PricesPage] loadPrices failed:", e);
      const msg = e.message === "Failed to fetch"
        ? "Backend niet bereikbaar — is de Flask-server actief op poort 5000?"
        : e.message;
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [status?.country]);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => { loadPrices(source); }, [source, loadPrices]);

  const handleLogin = () => { loadStatus(); setData(null); loadPrices(source); };

  const switchSource = (s) => {
    saveSource(s);
    setData(null);
    // loadPrices will be triggered by the source useEffect
  };

  // Raw prices for current day
  const rawPrices = data ? (day === "today" ? data.today : data.tomorrow) : [];
  const hasTomorrow = data?.tomorrow?.length > 0;

  // Detect whether raw data has 15-min granularity
  const dataIs15min = detectResolution(rawPrices);

  // Apply resolution: aggregate to hourly if 15-min data + user wants hours
  const prices = (() => {
    if (!rawPrices?.length) return [];
    if (dataIs15min === "15min" && resolution === "1h") return aggregateToHourly(rawPrices);
    return rawPrices;
  })();

  // Is the displayed data 15-min?
  const showing15min = detectResolution(prices) === "15min";

  const now = new Date();
  const currentPrice = prices?.find((p) => {
    const f = new Date(p.from), t = new Date(p.till);
    return now >= f && now < t;
  });

  return (
    <div className="prices-page">
      {/* Login panel */}
      <LoginPanel status={status} onLogin={handleLogin} />

      {/* Current price banner */}
      {currentPrice && (
        <div className="current-price-banner">
          <div className="current-price-label">Huidig tarief</div>
          <div className="current-price-value"
            style={{ color: priceColor(total(currentPrice),
              Math.min(...prices.map(total)),
              Math.max(...prices.map(total))) }}>
            {fmtCt(total(currentPrice))}
            <span className="current-price-unit">/kWh</span>
          </div>
          <div className="current-price-period">
            {hhmm(currentPrice.from)} – {hhmm(currentPrice.till)}
          </div>
        </div>
      )}

      {/* Day tabs + view toggle */}
      <div className="prices-toolbar">
        <div className="tab-bar" style={{ borderBottom: "none" }}>
          <button className={`tab-btn ${day === "today" ? "active" : ""}`}
            onClick={() => setDay("today")}>Vandaag</button>
          <button className={`tab-btn ${day === "tomorrow" ? "active" : ""}`}
            onClick={() => setDay("tomorrow")}
            disabled={!hasTomorrow}
            title={!hasTomorrow ? "Morgen prijzen nog niet beschikbaar" : ""}>
            Morgen {!hasTomorrow && <span style={{ fontSize: 10, opacity: .6 }}>(n.b.)</span>}
          </button>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {/* Source picker */}
          <div className="btn-group">
            <button
              className={`btn btn-sm ${source === "frank" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => switchSource("frank")}
              title="Frank Energie – persoonlijke of publieke uurprijzen">
              Frank Energie
            </button>
            <button
              className={`btn btn-sm ${source === "entsoe" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => switchSource("entsoe")}
              title="ENTSO-E Transparency Platform – kwartierprijzen (API sleutel vereist in Instellingen)">
              ENTSO-E
            </button>
          </div>

          {/* Resolution toggle */}
          <div className="btn-group">
            <button
              className={`btn btn-sm ${resolution === "15min" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => saveResolution("15min")}
              title={dataIs15min === "15min" ? "Kwartierprijzen (15 min)" : "Geen kwartierprijzen beschikbaar — gebruik ENTSO-E als bron"}
              disabled={dataIs15min !== "15min"}>
              15 min
            </button>
            <button
              className={`btn btn-sm ${resolution === "1h" ? "btn-primary" : "btn-ghost"}`}
              onClick={() => saveResolution("1h")}
              title="Uurprijzen">
              1 uur
            </button>
          </div>

          <button className={`btn btn-sm ${view === "chart" ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setView("chart")}>📊 Grafiek</button>
          <button className={`btn btn-sm ${view === "table" ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setView("table")}>📋 Tabel</button>
          <button className="btn btn-sm btn-ghost" onClick={() => loadPrices(source)} title="Vernieuwen">↻</button>
        </div>
      </div>

      {/* Content */}
      {loading && (
        <div className="loading-overlay" style={{ minHeight: 200 }}>
          <div className="loading-spinner" />
          <span>Prijzen laden…</span>
        </div>
      )}
      {error && (
        <div className="offline-banner">⚠ Fout: {error}</div>
      )}
      {!loading && !error && prices?.length > 0 && (
        <div className="prices-content">
          <PriceStats prices={prices} is15min={showing15min} />
          <div className="prices-chart-wrap">
            {view === "chart" ? (
              <PriceChart prices={prices} />
            ) : (
              <PriceTable prices={prices} is15min={showing15min} />
            )}
          </div>
          {/* Legend */}
          <div className="price-legend">
            <span><span className="legend-dot" style={{ background: "#22c55e" }} />Goedkoop</span>
            <span><span className="legend-dot" style={{ background: "#84cc16" }} />Normaal</span>
            <span><span className="legend-dot" style={{ background: "#f59e0b" }} />Duur</span>
            <span><span className="legend-dot" style={{ background: "#ef4444" }} />Zeer duur</span>
            <span style={{ color: "#64748b", fontSize: 11 }}>
              {data?.loggedIn ? "Persoonlijke tarieven" : "Marktprijzen (excl. persoonlijke opslag)"}
              {showing15min ? " · 15 min intervallen" : " · uurgemiddelden"}
            </span>
          </div>
        </div>
      )}
      {!loading && !error && prices?.length === 0 && (
        <div className="empty-state" style={{ minHeight: 200 }}>
          <div className="empty-state-icon">📭</div>
          <div className="empty-state-title">Geen prijzen beschikbaar</div>
          <div className="empty-state-desc">
            {day === "tomorrow"
              ? "Morgen prijzen worden doorgaans rond 14:00 gepubliceerd."
              : "Kon geen prijzen ophalen van Frank Energie."}
          </div>
        </div>
      )}
    </div>
  );
}
