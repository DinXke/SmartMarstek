import { useState, useEffect, useCallback } from "react";
import DeviceCard from "./components/DeviceCard.jsx";
import AddDeviceModal from "./components/AddDeviceModal.jsx";
import PricesPage from "./components/PricesPage.jsx";
import ForecastPage from "./components/ForecastPage.jsx";
import StrategyPage from "./components/StrategyPage.jsx";
import SettingsPage from "./components/SettingsPage.jsx";
import ProfitPage from "./components/ProfitPage.jsx";
import HistoricalFrankPage from "./components/HistoricalFrankPage.jsx";
import EnergyMap from "./components/EnergyMap.jsx";
import HomeWizardPanel from "./components/HomeWizardPanel.jsx";

const THEMES = [
  { id: "dark",   icon: "🌙", label: "Dark"   },
  { id: "light",  icon: "☀️", label: "Light"  },
  { id: "matrix", icon: "🟩", label: "Matrix" },
];

function useTheme() {
  const [theme, setThemeState] = useState(
    () => localStorage.getItem("marstek_theme") || "dark"
  );
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("marstek_theme", theme);
  }, [theme]);
  return [theme, setThemeState];
}

function useViewMode() {
  const [mode, setMode] = useState(
    () => localStorage.getItem("marstek_view") || "desktop"
  );
  useEffect(() => {
    document.documentElement.setAttribute("data-view", mode);
    localStorage.setItem("marstek_view", mode);
  }, [mode]);
  return [mode, setMode];
}

function useUiVersion() {
  const [version, setVersion] = useState(
    () => localStorage.getItem("marstek_ui") || "old"
  );
  useEffect(() => {
    document.documentElement.setAttribute("data-ui", version);
    localStorage.setItem("marstek_ui", version);
  }, [version]);
  return [version, setVersion];
}

function ViewToggle() {
  const [mode, setMode] = useViewMode();
  const isMobile = mode === "mobile";
  return (
    <button
      className="btn btn-ghost btn-sm"
      onClick={() => setMode(isMobile ? "desktop" : "mobile")}
      title={isMobile ? "Schakel naar desktopweergave" : "Schakel naar mobiele weergave"}
      style={{ gap: 4, fontSize: 12 }}
    >
      {isMobile ? "🖥️" : "📱"}
    </button>
  );
}

function UiVersionToggle() {
  const [version, setVersion] = useUiVersion();
  const isNew = version === "new";
  return (
    <button
      className="btn btn-ghost btn-sm"
      onClick={() => setVersion(isNew ? "old" : "new")}
      title={isNew ? "Schakel naar Old UI" : "Schakel naar New UI"}
      style={{ gap: 4, fontSize: 12 }}
    >
      {isNew ? "🆕" : "🕹️"} {isNew ? "New UI" : "Old UI"}
    </button>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const current = THEMES.find((t) => t.id === theme) || THEMES[0];
  const next    = THEMES[(THEMES.findIndex((t) => t.id === theme) + 1) % THEMES.length];
  return (
    <button
      className="btn btn-ghost btn-sm"
      onClick={() => setTheme(next.id)}
      title={`Schakel naar ${next.label} thema`}
      style={{ gap: 4, fontSize: 12 }}
    >
      {current.icon} {current.label}
    </button>
  );
}

const NAV_ITEMS = [
  { id: "batteries", icon: "🔋", label: "Batterijen"       },
  { id: "prices",    icon: "⚡", label: "Prijzen"           },
  { id: "forecast",  icon: "☀️", label: "Voorspelling"     },
  { id: "strategy",  icon: "🧠", label: "Strategie"        },
  { id: "profit",    icon: "💰", label: "Winst"             },
  { id: "frank",     icon: "📊", label: "Frank Historia"   },
  { id: "settings",  icon: "⚙️", label: "Instellingen"     },
];

export default function App() {
  // Apply saved theme + view mode + ui version immediately on mount
  useEffect(() => {
    const theme = localStorage.getItem("marstek_theme") || "dark";
    document.documentElement.setAttribute("data-theme", theme);
    const view = localStorage.getItem("marstek_view") || "desktop";
    document.documentElement.setAttribute("data-view", view);
    const ui = localStorage.getItem("marstek_ui") || "old";
    document.documentElement.setAttribute("data-ui", ui);
  }, []);
  const [page, setPage]       = useState("batteries");
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  // Aggregated power from all devices: { [deviceId]: { acPower, batPower, acVoltage, phaseVoltages } }
  const [powerMap, setPowerMap] = useState({});

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch("api/devices");
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
            <div className="app-header-title">Marstek</div>
            <div className="app-header-subtitle app-header-subtitle--desktop">ESPHome Battery Monitor</div>
          </div>
        </div>

        {/* Desktop navigation */}
        <nav className="app-nav app-nav--desktop">
          {NAV_ITEMS.map((n) => (
            <button key={n.id}
              className={`nav-btn ${page === n.id ? "active" : ""}`}
              onClick={() => setPage(n.id)}
              aria-current={page === n.id ? "page" : undefined}
            >
              {n.icon} {n.label}
            </button>
          ))}
        </nav>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ViewToggle />
          <ThemeToggle />
          <UiVersionToggle />
          {page === "batteries" && (
            <button className="btn btn-primary btn--add-desktop" onClick={() => setShowAdd(true)}>
              + Toevoegen
            </button>
          )}
        </div>
      </header>

      {/* ── Mobile bottom nav ── */}
      <nav className="app-nav--mobile">
        {NAV_ITEMS.map((n) => (
          <button key={n.id}
            className={`mobile-nav-btn ${page === n.id ? "active" : ""}`}
            onClick={() => setPage(n.id)}
            aria-current={page === n.id ? "page" : undefined}
          >
            <span className="mobile-nav-icon">{n.icon}</span>
            <span className="mobile-nav-label">{n.label}</span>
          </button>
        ))}
      </nav>

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
        {page === "profit"    && <ProfitPage />}
        {page === "frank"     && <HistoricalFrankPage />}

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

      {/* Mobile FAB for adding devices */}
      {page === "batteries" && (
        <button className="fab" onClick={() => setShowAdd(true)} title="Apparaat toevoegen">+</button>
      )}

      {showAdd && (
        <AddDeviceModal onClose={() => setShowAdd(false)} onAdded={handleDeviceAdded} />
      )}
    </>
  );
}
