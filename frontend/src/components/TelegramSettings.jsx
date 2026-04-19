import { useState, useEffect } from "react";

const EVENT_LABELS = {
  plan_ready:             "Dagplan klaar",
  grid_charge_opportunity: "Goedkoop laden (goedkeuring)",
  esphome_failed:         "ESPHome verbindingsfout",
  daily_summary:          "Dagelijks overzicht (~08:00)",
};

const DEFAULTS = {
  telegram_enabled:               false,
  telegram_chat_id:               "",
  telegram_events: {
    plan_ready:              true,
    grid_charge_opportunity: true,
    esphome_failed:          true,
    daily_summary:           true,
  },
  telegram_grid_price_threshold:  0.10,
  telegram_grid_soc_threshold:    80,
};

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

function Toggle({ on, onChange }) {
  return (
    <button className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)}
      aria-pressed={on} type="button" />
  );
}

export default function TelegramSettings() {
  const [vals,    setVals]    = useState(DEFAULTS);
  const [saving,  setSaving]  = useState(false);
  const [success, setSuccess] = useState(false);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    fetch("api/strategy/settings")
      .then((r) => r.json())
      .then((d) => setVals({
        ...DEFAULTS,
        telegram_enabled:              d.telegram_enabled              ?? DEFAULTS.telegram_enabled,
        telegram_chat_id:              d.telegram_chat_id              ?? DEFAULTS.telegram_chat_id,
        telegram_events:               { ...DEFAULTS.telegram_events,  ...(d.telegram_events  || {}) },
        telegram_grid_price_threshold: d.telegram_grid_price_threshold ?? DEFAULTS.telegram_grid_price_threshold,
        telegram_grid_soc_threshold:   d.telegram_grid_soc_threshold   ?? DEFAULTS.telegram_grid_soc_threshold,
      }))
      .catch(() => {});
  }, []);

  function setEvent(key, value) {
    setVals((v) => ({ ...v, telegram_events: { ...v.telegram_events, [key]: value } }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const r = await fetch("api/strategy/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          telegram_enabled:              vals.telegram_enabled,
          telegram_chat_id:              vals.telegram_chat_id,
          telegram_events:               vals.telegram_events,
          telegram_grid_price_threshold: parseFloat(vals.telegram_grid_price_threshold) || 0.10,
          telegram_grid_soc_threshold:   parseInt(vals.telegram_grid_soc_threshold)     || 80,
        }),
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

  return (
    <div className="settings-card">
      <div className="settings-card-title">Telegram-notificaties</div>

      <Row label="Telegram inschakelen">
        <Toggle on={vals.telegram_enabled} onChange={(v) => setVals((s) => ({ ...s, telegram_enabled: v }))} />
      </Row>

      <Row label="Chat ID" desc="Jouw Telegram chat_id of groeps-id (gebruik @userinfobot om dit op te vragen)">
        <input className="form-input" type="text" placeholder="896640302"
          value={vals.telegram_chat_id}
          onChange={(e) => setVals((s) => ({ ...s, telegram_chat_id: e.target.value }))}
          disabled={!vals.telegram_enabled}
          style={{ width: 160 }} />
      </Row>

      <div className="settings-row-label" style={{ marginTop: 16, marginBottom: 4, fontWeight: 600 }}>
        Notificaties per event
      </div>
      {Object.entries(EVENT_LABELS).map(([key, label]) => (
        <Row key={key} label={label}>
          <Toggle
            on={vals.telegram_events[key] ?? true}
            onChange={(v) => setEvent(key, v)}
          />
        </Row>
      ))}

      <Row
        label="Prijs-drempel laden (€/kWh)"
        desc="grid_charge_opportunity wordt verstuurd als de stroomprijs onder dit bedrag ligt">
        <input className="form-input" type="number" step="0.01" min="0" max="1"
          value={vals.telegram_grid_price_threshold}
          onChange={(e) => setVals((s) => ({ ...s, telegram_grid_price_threshold: e.target.value }))}
          disabled={!vals.telegram_enabled}
          style={{ width: 100 }} />
      </Row>

      <Row
        label="SoC-drempel laden (%)"
        desc="grid_charge_opportunity wordt verstuurd als de SoC onder deze drempel ligt">
        <input className="form-input" type="number" step="1" min="0" max="100"
          value={vals.telegram_grid_soc_threshold}
          onChange={(e) => setVals((s) => ({ ...s, telegram_grid_soc_threshold: e.target.value }))}
          disabled={!vals.telegram_enabled}
          style={{ width: 100 }} />
      </Row>

      <div style={{ marginTop: 16, display: "flex", gap: 10, alignItems: "center" }}>
        <button className="btn-primary" onClick={save} disabled={saving}>
          {saving ? "Opslaan…" : "Opslaan"}
        </button>
        {success && <span style={{ color: "#4ade80", fontSize: 13 }}>✓ Opgeslagen</span>}
        {error   && <span style={{ color: "#f87171", fontSize: 13 }}>{error}</span>}
      </div>
    </div>
  );
}
