import { useState, useEffect, useCallback } from "react";

const PERIODS = [
  { label: "Dag",   days: 1  },
  { label: "Week",  days: 7  },
  { label: "Maand", days: 30 },
];

function toIso(d) {
  return d.toISOString().split("T")[0];
}

function addDays(isoStr, n) {
  const d = new Date(isoStr + "T12:00:00");
  d.setDate(d.getDate() + n);
  return toIso(d);
}

function fmtDate(isoStr) {
  const d = new Date(isoStr + "T12:00:00");
  return d.toLocaleDateString("nl-BE", { day: "numeric", month: "short" });
}

function fmtDateRange(startIso, windowDays) {
  if (windowDays === 1) {
    const d = new Date(startIso + "T12:00:00");
    return d.toLocaleDateString("nl-BE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  }
  const endD  = new Date(addDays(startIso, windowDays - 1) + "T12:00:00");
  const startD = new Date(startIso + "T12:00:00");
  return `${startD.toLocaleDateString("nl-BE", { day: "numeric", month: "short" })} – ${endD.toLocaleDateString("nl-BE", { day: "numeric", month: "short", year: "numeric" })}`;
}

function aggregateData(data, windowDays) {
  if (windowDays === 1) return data;
  const grouped = {};
  data.forEach((point) => {
    const key = point.date;
    if (!grouped[key]) {
      grouped[key] = { date: key, label: fmtDate(key), frank_kwh: 0, frank_cost_eur: 0 };
    }
    grouped[key].frank_kwh      += point.frank_kwh      || 0;
    grouped[key].frank_cost_eur += point.frank_cost_eur || 0;
  });
  return Object.values(grouped).sort((a, b) => a.date.localeCompare(b.date));
}

export default function HistoricalFrankPage() {
  const today = toIso(new Date());

  const [windowDays, setWindowDays] = useState(1);
  const [startDate,   setStartDate]   = useState(today);
  const [consumption, setConsumption] = useState([]);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const [selectedIdx, setSelectedIdx] = useState(null);

  const endDate = addDays(startDate, windowDays - 1);
  const isToday = endDate >= today;

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`api/frank/consumption?startDate=${startDate}&endDate=${endDate}`);
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { msg = (await res.json()).error || msg; } catch (_) {}
        throw new Error(msg);
      }
      setConsumption(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handlePrev = () => { setSelectedIdx(null); setStartDate(prev => addDays(prev, -windowDays)); };
  const handleNext = () => { if (!isToday) { setSelectedIdx(null); setStartDate(prev => addDays(prev, windowDays)); } };

  const handlePeriod = (days) => {
    setSelectedIdx(null);
    setWindowDays(days);
    setStartDate(addDays(today, -(days - 1)));
  };

  const aggregated  = aggregateData(consumption, windowDays);
  const maxKwh      = aggregated.length > 0 ? Math.max(0.01, ...aggregated.map(c => c.frank_kwh || 0)) : 1;
  const totalFrank  = aggregated.reduce((s, c) => s + (c.frank_kwh      || 0), 0);
  const totalCost   = aggregated.reduce((s, c) => s + (c.frank_cost_eur || 0), 0);

  const slotMinWidth = windowDays === 1 ? 26 : 20;

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "12px 16px" }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>📊 Frank Verbruik</h2>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {PERIODS.map(p => (
            <button key={p.days} onClick={() => handlePeriod(p.days)} style={{
              padding: "5px 12px", borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: windowDays === p.days ? "var(--accent-color, #3b82f6)" : "var(--card-bg)",
              color: windowDays === p.days ? "#fff" : "var(--text-primary)",
              cursor: "pointer", fontSize: 12, fontWeight: windowDays === p.days ? 600 : 400,
            }}>{p.label}</button>
          ))}
          <button onClick={fetchData} title="Verversen" style={{
            padding: "5px 10px", borderRadius: 6, border: "1px solid var(--border-color)",
            background: "var(--card-bg)", color: "var(--text-muted)", cursor: "pointer", fontSize: 12,
          }}>↺</button>
        </div>
      </div>

      {/* Navigation */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <button onClick={handlePrev} style={navBtn}>← Vorige</button>
        <span style={{ fontSize: 14, fontWeight: 600, textAlign: "center", color: "var(--text-primary)" }}>
          {fmtDateRange(startDate, windowDays)}
        </span>
        <button onClick={handleNext} disabled={isToday} style={{ ...navBtn, opacity: isToday ? 0.35 : 1, cursor: isToday ? "default" : "pointer" }}>
          Volgende →
        </button>
      </div>

      {error && (
        <div style={{ padding: "10px 14px", background: "#fee2e2", color: "#b91c1c", border: "1px solid #fca5a5", borderRadius: 6, marginBottom: 12, fontSize: 13 }}>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, padding: "3rem", color: "var(--text-muted)", fontSize: 14 }}>
          <div style={{ width: 18, height: 18, border: "2px solid var(--border-color)", borderTopColor: "#3b82f6", borderRadius: "50%", animation: "frankSpin 0.7s linear infinite" }} />
          Gegevens laden…
        </div>
      ) : aggregated.length === 0 ? (
        <div style={{ textAlign: "center", padding: "3rem 1rem", color: "var(--text-muted)" }}>
          <div style={{ fontSize: 36, marginBottom: 10 }}>📊</div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Geen verbruiksgegevens</div>
          <div style={{ fontSize: 13 }}>Zorg dat je Frank ingelogd bent en probeer het opnieuw.</div>
        </div>
      ) : (
        <>
          {/* Chart */}
          <div style={{ background: "var(--card-bg, #f9fafb)", borderRadius: 8, padding: "16px 12px 8px", border: "1px solid var(--border-color)" }}>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 218, overflowX: "auto", paddingBottom: 38 }}>
              {aggregated.map((point, idx) => {
                const isSelected = selectedIdx === idx;
                const frankH = Math.max(0, (point.frank_kwh || 0) / maxKwh * 178);
                return (
                  <div key={idx}
                    onClick={() => setSelectedIdx(isSelected ? null : idx)}
                    style={{
                      display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
                      minWidth: slotMinWidth, flex: 1, cursor: "pointer",
                      outline: isSelected ? "2px solid #3b82f6" : "none", outlineOffset: 1, borderRadius: 3,
                    }}>
                    <div style={{ display: "flex", alignItems: "flex-end", height: 178, width: "100%" }}>
                      <div style={{
                        flex: 1,
                        height: `${frankH}px`,
                        minHeight: point.frank_kwh > 0 ? 2 : 0,
                        background: isSelected
                          ? "linear-gradient(to top,#1d4ed8,#3b82f6)"
                          : "linear-gradient(to top,#2563eb,#60a5fa)",
                        borderRadius: "3px 3px 0 0",
                        transition: "background 0.1s",
                      }} />
                    </div>
                    <div style={{
                      fontSize: 9, color: isSelected ? "#3b82f6" : "var(--text-muted)",
                      fontWeight: isSelected ? 700 : 400,
                      writingMode: "vertical-rl", transform: "rotate(180deg)",
                      whiteSpace: "nowrap", maxHeight: 36, overflow: "hidden",
                    }}>
                      {point.label || fmtDate(point.date)}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Selected bar detail */}
          {selectedIdx !== null && aggregated[selectedIdx] && (() => {
            const p = aggregated[selectedIdx];
            const fromLocal = p.from ? new Date(p.from).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" }) : null;
            const tillLocal = p.till ? new Date(p.till).toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" }) : null;
            const timeLabel = fromLocal && tillLocal ? `${fromLocal} – ${tillLocal}` : (p.label || fmtDate(p.date));
            const pricePerKwh = p.frank_kwh > 0 && p.frank_cost_eur > 0 ? (p.frank_cost_eur / p.frank_kwh) : null;
            return (
              <div style={{ margin: "10px 0", background: "var(--card-bg)", border: "2px solid #3b82f6", borderRadius: 8, overflow: "hidden" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 14px", background: "#3b82f620", borderBottom: "1px solid #3b82f640" }}>
                  <span style={{ fontWeight: 700, fontSize: 13, color: "#3b82f6" }}>{timeLabel}</span>
                  <button onClick={() => setSelectedIdx(null)} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16, lineHeight: 1 }}>✕</button>
                </div>
                <div style={{ padding: "8px 14px" }}>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 20px" }}>
                    <div style={{ fontSize: 13 }}><span style={{ color: "var(--text-muted)" }}>Verbruik: </span><strong>{(p.frank_kwh || 0).toFixed(3)} kWh</strong></div>
                    {p.frank_cost_eur > 0 && <div style={{ fontSize: 13 }}><span style={{ color: "var(--text-muted)" }}>Kosten: </span><strong>€ {p.frank_cost_eur.toFixed(4)}</strong></div>}
                    {pricePerKwh && <div style={{ fontSize: 13 }}><span style={{ color: "var(--text-muted)" }}>Prijs/kWh: </span><strong>€ {pricePerKwh.toFixed(4)}</strong></div>}
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Totals */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8, marginTop: 10 }}>
            {[
              { label: "Frank verbruik", value: `${totalFrank.toFixed(2)} kWh`, color: "#3b82f6" },
              ...(totalCost > 0 ? [{ label: "Frank kosten", value: `€ ${totalCost.toFixed(2)}`, color: "#8b5cf6" }] : []),
            ].map(item => (
              <div key={item.label} style={{
                background: "var(--card-bg)", border: "1px solid var(--border-color)",
                borderLeft: `3px solid ${item.color}`, borderRadius: 6, padding: "8px 12px",
              }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>{item.label}</div>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{item.value}</div>
              </div>
            ))}
          </div>
        </>
      )}

      <style>{`@keyframes frankSpin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

const navBtn = {
  padding: "6px 14px", borderRadius: 6,
  border: "1px solid var(--border-color)",
  background: "var(--card-bg)", color: "var(--text-primary)",
  cursor: "pointer", fontSize: 13, fontWeight: 500,
};
