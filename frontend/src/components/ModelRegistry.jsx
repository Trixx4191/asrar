import { useState, useEffect } from "react";

const API = "http://127.0.0.1:8000";

export default function ModelRegistry() {
  const [models, setModels] = useState([]);
  const [lookupVal, setLookupVal] = useState("");
  const [lookupStatus, setLookupStatus] = useState("");
  const [loading, setLoading] = useState(false);

  async function fetchModels() {
    try {
      const r = await fetch(`${API}/models`);
      const d = await r.json();
      setModels(d.models || []);
    } catch { setModels([]); }
  }

  useEffect(() => { fetchModels(); }, []);

  async function toggleModel(key, current) {
    await fetch(`${API}/models/${key}/toggle`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !current }),
    });
    fetchModels();
  }

  async function removeModel(key) {
    if (!confirm(`Remove model "${key}"?`)) return;
    await fetch(`${API}/models/${key}`, { method: "DELETE" });
    fetchModels();
  }

  async function lookupModel() {
    if (!lookupVal.trim()) return;
    setLoading(true);
    setLookupStatus("Looking up model...");
    try {
      const r = await fetch(`${API}/models/lookup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name_or_url: lookupVal.trim() }),
      });
      const d = await r.json();
      if (d.success) {
        setLookupStatus(`✓ Added: ${d.model.display_name}`);
        setLookupVal("");
        fetchModels();
      } else {
        setLookupStatus("Could not find model. Check the name or URL.");
      }
    } catch (e) {
      setLookupStatus(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Model Registry</div>
        <div className="panel-subtitle">
          {models.filter(m => m.enabled !== false).length} active · {models.length} total
        </div>
      </div>

      <div className="panel-body">
        {/* Add model */}
        <div className="add-model-box">
          <h4>Add a new model</h4>
          <input
            className="add-model-input"
            placeholder="e.g. qwen/qwen-2.5-72b or paste an OpenRouter URL"
            value={lookupVal}
            onChange={e => setLookupVal(e.target.value)}
            onKeyDown={e => e.key === "Enter" && lookupModel()}
          />
          <button className="primary-btn" onClick={lookupModel} disabled={loading || !lookupVal.trim()}>
            {loading ? "Looking up..." : "Look up & add"}
          </button>
          {lookupStatus && (
            <div style={{ fontSize: 12, color: lookupStatus.startsWith("✓") ? "var(--green)" : "var(--red)" }}>
              {lookupStatus}
            </div>
          )}
        </div>

        {/* Model list */}
        {models.map(m => (
          <div key={m.key} className={`model-card ${m.enabled === false ? "disabled" : ""}`}>
            <div style={{ fontSize: 20 }}>
              {{ anthropic: "🟣", google: "🔵", groq: "🟡", deepseek: "🔴", openrouter: "⚪", mistral: "🟠" }[m.provider] || "⚫"}
            </div>
            <div className="model-card-info">
              <div className="model-card-name">{m.display_name}</div>
              <div className="model-card-meta">{m.provider} · {(m.context_window / 1000).toFixed(0)}k ctx</div>
              <div className="model-card-tags">
                {(m.strengths || []).slice(0, 3).map(s => (
                  <span key={s} className="tag">{s.replace("_", " ")}</span>
                ))}
              </div>
            </div>
            <div className="model-card-actions">
              <button
                className={`toggle-btn ${m.enabled !== false ? "active" : ""}`}
                onClick={() => toggleModel(m.key, m.enabled !== false)}
              >
                {m.enabled !== false ? "on" : "off"}
              </button>
              <button className="remove-btn" onClick={() => removeModel(m.key)}>✕</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
