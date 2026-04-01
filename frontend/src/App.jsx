import { useState, useEffect, useCallback } from "react";
import DeviceCard from "./components/DeviceCard.jsx";
import AddDeviceModal from "./components/AddDeviceModal.jsx";
import PricesPage from "./components/PricesPage.jsx";
import ForecastPage from "./components/ForecastPage.jsx";
import StrategyPage from "./components/StrategyPage.jsx";
import SettingsPage from "./components/SettingsPage.jsx";
import EnergyMap from "./components/EnergyMap.jsx";
import HomeWizardPanel from "./components/HomeWizardPanel.jsx";

export default function App() {
  const [page, setPage]       = useState("batteries"); // "batteries" | "prices" | "forecast" | "strategy" | "settings"
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  // Aggregated power from all devices: { [deviceId]: { acPower, batPower, acVoltage, phaseVoltages } }
  const [powerMap, setPowerMap] = useState({});

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch("/api/devices");
      if (res.ok) setDevices(await res.json());
    } catch { /* keep existing list */ }
    finally   { setLoading(false); }
  }, []);

  useEffect(() => { fetchDevices(); }, [fetchDevices]);

  const handleDeviceAdded   = (device)  => { setDevices((p) => [...p, device]); };
  const handleDeviceEdited  = (updated) => { setDevices((p) => p.map((d) => d.id === updated.id ? updated : d)); };
  const handleDeviceDeleted = (id)      => {
    setDevices((p) => p.filter((d) => d.id !== id));
    setPowerMap((p) => { const n = { ...p }; delete n[id]; return n; });
  };

  const handlePowerUpdate = useCallback((deviceId, data) => {
    setPowerMap((prev) => {
      const cur = prev[deviceId];
      // Avoid re-render if values haven't changed
      if (cur &&
          cur.acPower === data.acPower &&
          cur.batPower === data.batPower &&
          cur.acVoltage === data.acVoltage) return prev;
      return { ...prev, [deviceId]: data };
    });
  }, []);

  // Build batteries array for HomeFlow
  const homeFlowBatteries = devices.map((d) => ({
    id: d.id,
    name: d.name,
    ...(powerMap[d.id] ?? {}),
  }));

  // Use first available phase voltages / acVoltage for the home flow
  const firstWithPhase = Object.values(powerMap).find((p) => p.phaseVoltages);
  const firstWithVolt  = Object.values(powerMap).find((p) => p.acVoltage != null);

  return (
    <>
      {/* ── Header ── */}
      <header className="app-header">
        <div className="app-header-brand">
          <span className="app-header-logo">🔋</span>
          <div>
            <div className="app-header-title">Marstek Dashboard</div>
            <div className="app-header-subtitle">ESPHome Battery Monitor</div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="app-nav">
          <button
            className={`nav-btn ${page === "batteries" ? "active" : ""}`}
            onClick={() => setPage("batteries")}
          >
            🔋 Batterijen
          </button>
          <button
            className={`nav-btn ${page === "prices" ? "active" : ""}`}
            onClick={() => setPage("prices")}
          >
            ⚡ Energieprijzen
          </button>
          <button
            className={`nav-btn ${page === "forecast" ? "active" : ""}`}
            onClick={() => setPage("forecast")}
          >
            ☀️ Zonne-voorspelling
          </button>
          <button
            className={`nav-btn ${page === "strategy" ? "active" : ""}`}
            onClick={() => setPage("strategy")}
          >
            🧠 Laadstrategie
          </button>
          <button
            className={`nav-btn ${page === "settings" ? "active" : ""}`}
            onClick={() => setPage("settings")}
          >
            ⚙️ Instellingen
          </button>
        </nav>

        {page === "batteries" && (
          <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
            + Toevoegen
          </button>
        )}
      </header>

      {/* ── Main ── */}
      <main className="app-main">
        {page === "batteries" && (
          loading ? (
            <div className="loading-overlay">
              <div className="loading-spinner" />
              <span>Apparaten laden…</span>
            </div>
          ) : devices.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🔋</div>
              <div className="empty-state-title">Nog geen apparaten</div>
              <div className="empty-state-desc">
                Voeg een Marstek batterij toe om te beginnen met monitoren.
              </div>
              <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
                + Apparaat toevoegen
              </button>
            </div>
          ) : (
            <>
              {/* ── Aggregated home flow ── */}
              <div className="home-flow-card">
                <div className="home-flow-card-title">⚡ Vermogensbalans</div>
                <EnergyMap
                  batteries={homeFlowBatteries}
                  phaseVoltages={firstWithPhase?.phaseVoltages ?? null}
                  acVoltage={firstWithVolt?.acVoltage ?? null}
                />
              </div>

              {/* ── HomeWizard panel ── */}
              <HomeWizardPanel />

              {/* ── Individual device cards ── */}
              <div className="device-grid">
                {devices.map((device) => (
                  <DeviceCard
                    key={device.id}
                    device={device}
                    onDelete={handleDeviceDeleted}
                    onEdit={handleDeviceEdited}
                    onPowerUpdate={handlePowerUpdate}
                  />
                ))}
              </div>
            </>
          )
        )}

        {page === "prices"    && <PricesPage />}
        {page === "forecast"  && <ForecastPage />}
        {page === "strategy"  && <StrategyPage />}

        {page === "settings" && (
          <SettingsPage
            devices={devices}
            powerMap={powerMap}
            onDeviceAdded={handleDeviceAdded}
            onDeviceEdited={handleDeviceEdited}
            onDeviceDeleted={handleDeviceDeleted}
          />
        )}
      </main>

      {showAdd && (
        <AddDeviceModal onClose={() => setShowAdd(false)} onAdded={handleDeviceAdded} />
      )}
    </>
  );
}
