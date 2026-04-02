import { useState, useEffect, useCallback } from "react";

// ---------------------------------------------------------------------------
// HomeAssistantSettings – configure HA long-lived access token + URL
// ---------------------------------------------------------------------------

export default function HomeAssistantSettings() {
  const [url,        setUrl]        = useState("http://homeassistant:8123");
  const [token,      setToken]      = useState("");
  const [configured, setConfigured] = useState(false);
  const [tokenHint,  setTokenHint]  = useState("");
  const [saving,     setSaving]     = useState(false);
  const [testing,    setTesting]    = useState(false);
  const [saveOk,     setSaveOk]     = useState(false);
  const [testResult, setTestResult] = useState(null); // null | { ok, message, version } | { error }
  const [error,      setError]      = useState(null);

  const loadSettings = useCallback(async () => {
    try {
      const r = await fetch("api/ha/settings");
      if (!r.ok) return;
      const d = await r.json();
      setConfigured(d.configured);
      setTokenHint(d.tokenHint || "");
      if (d.url) setUrl(d.url);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  const handleSave = async () => {
    if (!url.trim()) { setError("URL is verplicht."); return; }
    setSaving(true); setError(null); setSaveOk(false); setTestResult(null);
    try {
      const body = { url: url.trim() };
      if (token.trim()) body.token = token.trim();
      const r = await fetch("api/ha/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Opslaan mislukt.");
      if (token.trim()) {
        setConfigured(true);
        setTokenHint(`…${token.trim().slice(-4)}`);
        setToken("");
      }
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 3000);
    } catch (e) {
      setError(e.message);
    }
    setSaving(false);
  };

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setError(null);
    try {
      const r = await fetch("api/ha/test", { method: "POST" });
      const d = await r.json();
      setTestResult(d);
    } catch (e) {
      setTestResult({ error: e.message });
    }
    setTesting(false);
  };

  return (
    <div className="settings-section">
      <div className="settings-section-title">🏠 Home Assistant</div>

      <div style={{ padding: "4px 20px 4px", fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
        Koppel met Home Assistant via een Long-Lived Access Token om HA-sensoren te gebruiken
        als bron in het vermogensstroom-schema.
      </div>

      {/* URL */}
      <div className="settings-row">
        <div>
          <div className="settings-row-label">Home Assistant URL</div>
          <div className="settings-row-desc">Standaard correct als add-on. Pas aan bij externe toegang.</div>
        </div>
        <input
          className="form-input"
          style={{ flex: "1 1 260px", maxWidth: 320 }}
          type="url"
          placeholder="http://homeassistant:8123"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </div>

      {/* Token */}
      <div className="settings-row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 10 }}>
        <div>
          <div className="settings-row-label">Long-Lived Access Token</div>
          <div className="settings-row-desc">
            Maak aan in HA via Profiel → Beveiliging → Langlevende toegangstokens.
          </div>
        </div>

        {configured && (
          <div style={{ fontSize: 12, color: "var(--green)" }}>
            ✅ Token geconfigureerd ({tokenHint})
          </div>
        )}

        {error      && <div className="form-error">{error}</div>}
        {saveOk     && <div style={{ fontSize: 12, color: "var(--green)" }}>✓ Opgeslagen</div>}
        {testResult && (
          <div style={{ fontSize: 12, color: testResult.ok ? "var(--green)" : "var(--red)" }}>
            {testResult.ok
              ? `✅ Verbinding OK — HA ${testResult.version || ""} — ${testResult.message}`
              : `❌ ${testResult.error}`}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            className="form-input"
            type="password"
            placeholder={configured ? "Nieuw token (optioneel)" : "Plak hier je token"}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            style={{ flex: "1 1 280px" }}
          />
          <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
            {saving ? "Opslaan…" : "Opslaan"}
          </button>
          {configured && (
            <button className="btn btn-ghost btn-sm" onClick={handleTest} disabled={testing || !configured}>
              {testing ? "Testen…" : "Verbinding testen"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
