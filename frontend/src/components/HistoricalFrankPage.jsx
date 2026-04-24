import { useState, useEffect } from "react";

function HistoricalFrankPage() {
  const [consumption, setConsumption] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().split("T")[0];
  });
  const [endDate, setEndDate] = useState(
    new Date().toISOString().split("T")[0]
  );
  const [zoomLevel, setZoomLevel] = useState(1);
  const [granularity, setGranularity] = useState("day"); // "hour", "day", "week", "month"
  const [debugInfo, setDebugInfo] = useState(null);

  const fetchConsumption = async () => {
    setLoading(true);
    setError(null);
    setDebugInfo(null);
    try {
      const res = await fetch(
        `api/frank/consumption?startDate=${startDate}&endDate=${endDate}`
      );
      if (!res.ok) {
        let errorDetail = `HTTP ${res.status}`;
        try {
          const errorData = await res.json();
          errorDetail = errorData.error || errorDetail;
        } catch (e) {
          // If response isn't JSON, just use status
        }
        setDebugInfo({
          timestamp: new Date().toLocaleTimeString(),
          status: res.status,
          url: `api/frank/consumption?startDate=${startDate}&endDate=${endDate}`,
          error: errorDetail
        });
        throw new Error(errorDetail);
      }
      const data = await res.json();
      setConsumption(data);
      setDebugInfo({ timestamp: new Date().toLocaleTimeString(), records: data.length, status: "OK" });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConsumption();
  }, [startDate, endDate]);

  const handlePrevious = () => {
    const start = new Date(startDate);
    const end = new Date(endDate);
    const days = Math.floor((end - start) / (1000 * 60 * 60 * 24));
    start.setDate(start.getDate() - days);
    end.setDate(end.getDate() - days);
    setStartDate(start.toISOString().split("T")[0]);
    setEndDate(end.toISOString().split("T")[0]);
  };

  const handleNext = () => {
    const start = new Date(startDate);
    const end = new Date(endDate);
    const days = Math.floor((end - start) / (1000 * 60 * 60 * 24));
    start.setDate(start.getDate() + days);
    end.setDate(end.getDate() + days);
    const now = new Date();
    if (end <= now) {
      setStartDate(start.toISOString().split("T")[0]);
      setEndDate(end.toISOString().split("T")[0]);
    }
  };

  const handleZoom = (direction) => {
    const newZoom = direction === "in" ? zoomLevel + 0.2 : Math.max(0.5, zoomLevel - 0.2);
    setZoomLevel(newZoom);
  };

  const aggregateData = (data, gran) => {
    if (gran === "hour") return data;

    const grouped = {};
    data.forEach((point) => {
      let key;
      if (gran === "day") {
        key = point.date;
      } else if (gran === "week") {
        const d = new Date(point.date);
        const weekStart = new Date(d);
        weekStart.setDate(d.getDate() - d.getDay());
        key = weekStart.toISOString().split("T")[0];
      } else if (gran === "month") {
        key = point.date.substring(0, 7); // YYYY-MM
      }

      if (!grouped[key]) {
        grouped[key] = { date: key, frank_kwh: 0, p1_import_kwh: 0, p1_export_kwh: 0, label: key };
      }
      grouped[key].frank_kwh += point.frank_kwh || 0;
      grouped[key].p1_import_kwh += point.p1_import_kwh || 0;
      grouped[key].p1_export_kwh += point.p1_export_kwh || 0;
    });

    return Object.values(grouped).sort((a, b) => a.date.localeCompare(b.date));
  };

  const aggregated = aggregateData(consumption, granularity);
  const maxValue = aggregated.length > 0
    ? Math.max(
        ...aggregated.map((c) => c.frank_kwh || 0),
        ...aggregated.map((c) => c.p1_import_kwh || 0)
      )
    : 100;

  return (
    <div className="historical-frank-page">
      <div className="historical-frank-header">
        <h2>📊 Historische Frank Data + P1 Verbruik</h2>
        <div className="historical-frank-controls">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            max={endDate}
          />
          <span>tot</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            min={startDate}
          />
        </div>
      </div>

      <div className="historical-frank-toolbar">
        <button className="btn btn-ghost btn-sm" onClick={handlePrevious}>
          ← Vorige
        </button>
        <div className="granularity-controls">
          {["hour", "day", "week", "month"].map((g) => (
            <button
              key={g}
              className={`btn btn-sm ${granularity === g ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setGranularity(g)}
            >
              {g === "hour" ? "Uur" : g === "day" ? "Dag" : g === "week" ? "Week" : "Maand"}
            </button>
          ))}
        </div>
        <div className="zoom-controls">
          <button className="btn btn-ghost btn-sm" onClick={() => handleZoom("out")}>
            🔍−
          </button>
          <span>{(zoomLevel * 100).toFixed(0)}%</span>
          <button className="btn btn-ghost btn-sm" onClick={() => handleZoom("in")}>
            🔍+
          </button>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={handleNext}>
          Volgende →
        </button>
      </div>

      {error && (
        <div className="alert alert-error">
          <span>{error}</span>
        </div>
      )}

      {debugInfo && (
        <div className="debug-panel">
          <div className="debug-header">🔍 Debug Info</div>
          <div className="debug-content">
            {debugInfo.error ? (
              <>
                <div className="debug-line"><strong>Error:</strong> {debugInfo.error}</div>
                <div className="debug-line"><strong>Status:</strong> {debugInfo.status}</div>
                <div className="debug-line"><strong>URL:</strong> {debugInfo.url}</div>
              </>
            ) : (
              <>
                <div className="debug-line"><strong>Status:</strong> {debugInfo.status}</div>
                <div className="debug-line"><strong>Records:</strong> {debugInfo.records}</div>
              </>
            )}
            <div className="debug-line"><strong>Time:</strong> {debugInfo.timestamp}</div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="loading-overlay">
          <div className="loading-spinner" />
          <span>Gegevens laden…</span>
        </div>
      ) : consumption.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">📊</div>
          <div className="empty-state-title">Geen verbruiksgegevens</div>
          <div className="empty-state-desc">
            Zorg dat je Frank ingelogd bent en probeer het opnieuw.
          </div>
        </div>
      ) : (
        <div className="historical-frank-chart" style={{ transform: `scale(${zoomLevel})`, transformOrigin: "top center" }}>
          <div className="chart-bars">
            {aggregated.map((point, idx) => (
              <div key={idx} className="chart-bar-container" title={`${point.date}: Frank ${point.frank_kwh?.toFixed(2) || 0} kWh, P1 Import ${point.p1_import_kwh?.toFixed(2) || 0} kWh`}>
                <div className="chart-bar-wrapper">
                  <div
                    className="chart-bar chart-bar-frank"
                    style={{
                      height: `${((point.frank_kwh || 0) / maxValue) * 300}px`,
                    }}
                  />
                  <div
                    className="chart-bar chart-bar-p1"
                    style={{
                      height: `${((point.p1_import_kwh || 0) / maxValue) * 300}px`,
                    }}
                  />
                </div>
                <div className="chart-label">{point.label}</div>
              </div>
            ))}
          </div>
          <div className="chart-legend">
            <span className="legend-item"><span className="legend-color frank"></span>Frank</span>
            <span className="legend-item"><span className="legend-color p1"></span>P1 Import</span>
          </div>
          <div className="chart-info">
            <p>
              <strong>Frank totaal:</strong> {aggregated.reduce((sum, c) => sum + (c.frank_kwh || 0), 0).toFixed(2)} kWh
            </p>
            <p>
              <strong>P1 Import totaal:</strong> {aggregated.reduce((sum, c) => sum + (c.p1_import_kwh || 0), 0).toFixed(2)} kWh
            </p>
            <p>
              <strong>P1 Export totaal:</strong> {aggregated.reduce((sum, c) => sum + (c.p1_export_kwh || 0), 0).toFixed(2)} kWh
            </p>
          </div>
        </div>
      )}

      <style>{`
        .historical-frank-page {
          display: flex;
          flex-direction: column;
          gap: 1rem;
          padding: 1rem;
        }

        .historical-frank-header {
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }

        .historical-frank-header h2 {
          margin: 0;
          font-size: 1.5rem;
        }

        .historical-frank-controls {
          display: flex;
          gap: 1rem;
          align-items: center;
          flex-wrap: wrap;
        }

        .historical-frank-controls input {
          padding: 0.5rem;
          border: 1px solid var(--border-color, #ccc);
          border-radius: 0.25rem;
          font-family: inherit;
        }

        .historical-frank-toolbar {
          display: flex;
          gap: 1rem;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
        }

        .granularity-controls {
          display: flex;
          gap: 0.5rem;
          align-items: center;
        }

        .zoom-controls {
          display: flex;
          gap: 0.5rem;
          align-items: center;
        }

        .historical-frank-chart {
          background: var(--bg-secondary, #f5f5f5);
          border-radius: 0.5rem;
          padding: 2rem 1rem 1rem 1rem;
          min-height: 400px;
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }

        .chart-bars {
          display: flex;
          gap: 0.5rem;
          align-items: flex-end;
          height: 300px;
          overflow-x: auto;
          padding-bottom: 1rem;
        }

        .chart-bar-container {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 0.5rem;
          min-width: 40px;
          cursor: pointer;
        }

        .chart-bar-wrapper {
          position: relative;
          width: 100%;
          height: 300px;
          display: flex;
          align-items: flex-end;
          gap: 0.1rem;
        }

        .chart-bar {
          flex: 1;
          border-radius: 0.25rem 0.25rem 0 0;
          min-height: 2px;
          box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }

        .chart-bar-frank {
          background: linear-gradient(to top, #3b82f6, #60a5fa);
        }

        .chart-bar-p1 {
          background: linear-gradient(to top, #f59e0b, #fbbf24);
          opacity: 0.7;
        }

        .chart-bar-container:hover .chart-bar-frank {
          background: linear-gradient(to top, #2563eb, #3b82f6);
        }

        .chart-bar-container:hover .chart-bar-p1 {
          opacity: 1;
        }

        .chart-label {
          font-size: 0.75rem;
          writing-mode: vertical-rl;
          transform: rotate(180deg);
          white-space: nowrap;
        }

        .chart-legend {
          display: flex;
          gap: 2rem;
          padding: 0.5rem 1rem;
          font-size: 0.85rem;
        }

        .legend-item {
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }

        .legend-color {
          display: inline-block;
          width: 12px;
          height: 12px;
          border-radius: 0.15rem;
        }

        .legend-color.frank {
          background: linear-gradient(to top, #3b82f6, #60a5fa);
        }

        .legend-color.p1 {
          background: linear-gradient(to top, #f59e0b, #fbbf24);
        }

        .chart-info {
          padding: 1rem;
          background: var(--bg-tertiary, #fff);
          border-radius: 0.25rem;
          border-left: 4px solid #3b82f6;
        }

        .chart-info p {
          margin: 0.5rem 0;
          font-size: 0.9rem;
        }

        .chart-info p:first-child {
          margin-top: 0;
        }

        .chart-info p:last-child {
          margin-bottom: 0;
        }

        .empty-state {
          padding: 3rem 1rem;
          text-align: center;
        }

        .empty-state-icon {
          font-size: 3rem;
          margin-bottom: 1rem;
        }

        .empty-state-title {
          font-size: 1.25rem;
          font-weight: bold;
          margin-bottom: 0.5rem;
        }

        .empty-state-desc {
          color: var(--text-secondary, #666);
          margin-bottom: 1rem;
        }

        .alert {
          padding: 1rem;
          border-radius: 0.25rem;
          margin: 1rem;
        }

        .alert-error {
          background: #fee;
          color: #c33;
          border: 1px solid #fcc;
        }

        .debug-panel {
          background: #f0f0f0;
          border: 1px solid #999;
          border-radius: 0.25rem;
          padding: 0.5rem;
          margin: 1rem;
          font-family: monospace;
          font-size: 0.85rem;
        }

        .debug-header {
          font-weight: bold;
          margin-bottom: 0.5rem;
          color: #666;
        }

        .debug-content {
          display: flex;
          flex-direction: column;
          gap: 0.25rem;
        }

        .debug-line {
          color: #333;
          word-break: break-all;
        }

        .debug-line strong {
          color: #000;
          margin-right: 0.5rem;
        }

        @media (max-width: 640px) {
          .historical-frank-controls {
            flex-direction: column;
          }

          .historical-frank-toolbar {
            flex-direction: column;
          }

          .chart-bars {
            height: 200px;
          }

          .chart-label {
            font-size: 0.65rem;
          }
        }
      `}</style>
    </div>
  );
}

export default HistoricalFrankPage;
