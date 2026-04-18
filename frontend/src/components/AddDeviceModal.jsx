import React, { useState } from "react";

export default function AddDeviceModal({ onClose, onAdded }) {
  const [name, setName] = useState("");
  const [ip, setIp] = useState("");
  const [port, setPort] = useState("80");
  const [minSoc, setMinSoc] = useState("");
  const [maxSoc, setMaxSoc] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (!name.trim() || !ip.trim()) {
      setError("Name and IP address are required.");
      return;
    }

    const body = { name: name.trim(), ip: ip.trim(), port: parseInt(port) || 80 };
    if (minSoc !== "") body.min_soc = parseInt(minSoc);
    if (maxSoc !== "") body.max_soc = parseInt(maxSoc);

    setSaving(true);
    try {
      const res = await fetch("api/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Failed to add device.");
      } else {
        onAdded(data);
      }
    } catch {
      setError("Network error. Is the backend running?");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Add Device">
        <div className="modal-header">
          <span className="modal-title">Add Device</span>
          <button className="btn-icon btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="form-group">
              <label className="form-label" htmlFor="dev-name">Device Name</label>
              <input
                id="dev-name"
                className="form-input"
                placeholder="e.g. Garage Battery"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="dev-ip">IP Address</label>
              <input
                id="dev-ip"
                className="form-input"
                placeholder="e.g. 192.168.1.100"
                value={ip}
                onChange={(e) => setIp(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="dev-port">Port</label>
              <input
                id="dev-port"
                className="form-input"
                type="number"
                min="1"
                max="65535"
                placeholder="80"
                value={port}
                onChange={(e) => setPort(e.target.value)}
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label" htmlFor="dev-min-soc">Min SoC % <span style={{ fontWeight: 400, color: "var(--text-muted)" }}>(optioneel)</span></label>
                <input
                  id="dev-min-soc"
                  className="form-input"
                  type="number"
                  min="0"
                  max="50"
                  placeholder="bijv. 15"
                  value={minSoc}
                  onChange={(e) => setMinSoc(e.target.value)}
                />
              </div>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label" htmlFor="dev-max-soc">Max SoC % <span style={{ fontWeight: 400, color: "var(--text-muted)" }}>(optioneel)</span></label>
                <input
                  id="dev-max-soc"
                  className="form-input"
                  type="number"
                  min="50"
                  max="100"
                  placeholder="bijv. 95"
                  value={maxSoc}
                  onChange={(e) => setMaxSoc(e.target.value)}
                />
              </div>
            </div>
            {error && <div className="form-error">{error}</div>}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Adding…" : "Add Device"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
