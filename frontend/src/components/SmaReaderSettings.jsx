import { useState, useEffect } from "react";

const DEFAULTS = {
  sma_reader_enabled:    false,
  sma_reader_host:       "",
  sma_reader_port:       502,
  sma_reader_unit_id:    3,
  sma_reader_interval_s: 10,
  sma_reader_max_w:      4000,
};

function Toggle({ on, onChange }) {
  return (
    <button className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)}
      aria-pressed={on} type="button" />
  );
}

function Row({ label, desc, children }) {
  return (
    <div className="settings-row">
      <div>
        <div className="settings-row-label">{label}</div>
        {desc && <div className="settings-row-desc">{desc}</div>}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}

export default function SmaReaderSettings() {
  const [vals,    setVals]    = useState(DEFAULTS);
  const [saving,  setSaving]  = useState(false);
  const [success, setSuccess] = useState(false);
  const [error,   setError]   = useState(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => {
    fetch("api/strategy/settings")
      .then((r) => r.json())
      .then((d) => {
        setVals({
          sma_reader_enabled:    d.sma_reader_enabled    ?? DEFAULTS.sma_reader_enabled,
          sma_reader_host:       d.sma_reader_host       ?? DEFAULTS.sma_reader_host,
          sma_reader_port:       d.sma_reader_port       ?? DEFAULTS.sma_reader_port,
          sma_reader_unit_id:    d.sma_reader_unit_id    ?? DEFAULTS.sma_reader_unit_id,
          sma_reader_interval_s: d.sma_reader_interval_s ?? DEFAULTS.sma_reader_interval_s,
          sma_reader_max_w:      d.sma_reader_max_w      ?? DEFAULTS.sma_reader_max_w,
        });
      })
      .catch(() => {});
  }, []);

  function patch(key, value) {
    setVals((v) => ({ ...v, [key]: value }));
    setSuccess(false);
  }

  async function save() {
    setSaving(true); setError(null); setSuccess(false);
    try {
      const r = await fetch("api/strategy/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(vals),
      });
      if (!r.ok) throw new Error(await r.text());
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true); setTestResult(null);
    // Save current settings first so the test uses the entered host
    try {
      await fetch("api/strategy/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(vals),
      });
      const r = await fetch("api/sma/test", { method: "POST" });
      const d = await r.json();
      setTestResult(d);
    } catch (e) {
      setTestResult({ error: e.message });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section-title">☀️ SMA Omvormer — Modbus Reader</div>
      <p className="settings-section-desc" style={{ marginBottom: 16, fontSize: 13, color: "var(--text-muted)" }}>
        Lees live data van de SMA Sunny Boy terug via Modbus TCP: AC-vermogen, dagopbrengst,
        netspanning, frequentie en DC-spanning. De data wordt gebruikt als solar-bron in het
        stroomvlak en opgeslagen in InfluxDB.
      </p>

      <Row label="Ingeschakeld" desc="Polling activeren">
        <Toggle on={vals.sma_reader_enabled} onChange={(v) => patch("sma_reader_enabled", v)} />
      </Row>

      <Row label="IP-adres omvormer" desc="Let op: dit kan afwijken van het PV Limiter IP (bv. .141 vs .142)">
        <input className="form-input" style={{ width: 200 }}
          placeholder="192.168.1.x"
          value={vals.sma_reader_host}
          onChange={(e) => patch("sma_reader_host", e.target.value)} />
      </Row>

      <Row label="Poort" desc="Standaard 502 (Modbus TCP)">
        <input className="form-input" style={{ width: 80 }} type="number" min={1} max={65535}
          value={vals.sma_reader_port}
          onChange={(e) => patch("sma_reader_port", Number(e.target.value))} />
      </Row>

      <Row label="Unit ID" desc="SMA standaard = 3">
        <input className="form-input" style={{ width: 80 }} type="number" min={1} max={247}
          value={vals.sma_reader_unit_id}
          onChange={(e) => patch("sma_reader_unit_id", Number(e.target.value))} />
      </Row>

      <Row label="Pollinterval (seconden)" desc="Hoe vaak data wordt uitgelezen (5–60 s)">
        <input className="form-input" style={{ width: 80 }} type="number" min={5} max={60}
          value={vals.sma_reader_interval_s}
          onChange={(e) => patch("sma_reader_interval_s", Number(e.target.value))} />
      </Row>

      <Row label="Max vermogen (W)" desc="Nominaal piekvermorgen — gebruikt voor strategie-logica">
        <input className="form-input" style={{ width: 100 }} type="number" min={100} max={20000} step={100}
          value={vals.sma_reader_max_w}
          onChange={(e) => patch("sma_reader_max_w", Number(e.target.value))} />
      </Row>

      <div style={{ display: "flex", gap: 10, marginTop: 20, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? "Opslaan…" : "Opslaan"}
        </button>
        <button className="btn btn-secondary" onClick={testConnection} disabled={testing}
          style={{ opacity: vals.sma_reader_host ? 1 : 0.5 }}>
          {testing ? "Verbinden…" : "Verbinding testen"}
        </button>
        {success && <span style={{ color: "var(--success)", fontSize: 13 }}>✓ Opgeslagen</span>}
        {error   && <span style={{ color: "var(--danger)",  fontSize: 13 }}>{error}</span>}
      </div>

      {testResult && (
        <div style={{
          marginTop: 16, padding: "12px 16px", borderRadius: 8,
          background: testResult.online
            ? testResult.night_mode ? "rgba(255,214,0,.06)" : "rgba(0,230,100,.08)"
            : "rgba(255,80,80,.08)",
          border: `1px solid ${testResult.online
            ? testResult.night_mode ? "rgba(255,214,0,.35)" : "var(--success)"
            : "var(--danger)"}`,
          fontSize: 13,
        }}>
          {testResult.error && !testResult.online ? (
            <span style={{ color: "var(--danger)" }}>Verbinding mislukt: {testResult.error}</span>
          ) : testResult.online ? (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 8,
                color: testResult.night_mode ? "#ffd600" : "var(--success)" }}>
                {testResult.night_mode ? "🌙 Verbinding OK — omvormer in nachtmodus" : "✓ Verbinding geslaagd"}
              </div>

              {testResult.night_mode && (
                <div style={{ marginBottom: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  {testResult.night_mode_msg}
                </div>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 16px", marginBottom: 12 }}>
                <span>AC-vermogen</span><span><strong>{testResult.pac_w != null ? testResult.pac_w + " W" : "— (nacht)"}</strong></span>
                <span>Dagopbrengst</span><span><strong>{testResult.e_day_wh != null ? (testResult.e_day_wh / 1000).toFixed(2) + " kWh" : "— (nacht)"}</strong></span>
                <span>Netspanning</span><span><strong>{testResult.grid_v != null ? testResult.grid_v + " V" : "— (nacht)"}</strong></span>
                <span>Frequentie</span><span><strong>{testResult.freq_hz != null ? testResult.freq_hz + " Hz" : "— (nacht)"}</strong></span>
                <span>DC-vermogen</span><span><strong>{testResult.dc_power_w != null ? testResult.dc_power_w + " W" : "— (nacht)"}</strong></span>
                <span>DC-spanning</span><span><strong>{testResult.dc_voltage_v != null ? testResult.dc_voltage_v + " V" : "— (nacht)"}</strong></span>
                <span>Status</span><span><strong>{testResult.status ?? "— (nacht)"}</strong></span>
              </div>

              {/* Raw register diagnostics */}
              {testResult.raw && Object.keys(testResult.raw).length > 0 && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer", color: "var(--text-muted)", fontSize: 12 }}>
                    🔍 Raw registerwaarden tonen
                  </summary>
                  <div style={{
                    marginTop: 8, fontFamily: "monospace", fontSize: 11,
                    background: "var(--bg)", padding: "8px 10px", borderRadius: 6,
                    overflowX: "auto",
                  }}>
                    {Object.entries(testResult.raw).map(([k, v]) => (
                      <div key={k} style={{ marginBottom: 3 }}>
                        <span style={{ color: "var(--text-muted)" }}>FC{v.fc} addr={v.addr}:</span>{" "}
                        <span style={{ color: v.nan ? "#ffa000" : v.value != null ? "var(--success)" : "var(--danger)" }}>
                          {v.status === "read_error" ? "READ ERROR" : v.nan ? `NaN (${(v.regs||[]).join(",")})` : `${v.value} (${(v.regs||[]).join(",")})`}
                        </span>{" "}
                        <span style={{ color: "var(--text-muted)" }}>← {k}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          ) : (
            <span style={{ color: "var(--danger)" }}>Verbinding mislukt — controleer IP en unit ID</span>
          )}
        </div>
      )}
    </div>
  );
}
