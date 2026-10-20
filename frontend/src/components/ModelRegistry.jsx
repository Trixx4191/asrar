import { useState, useEffect } from "react";

const API = "http://127.0.0.1:8000";

export default function ModelRegistry() {
  const [models, setModels] = useState([]);
  const [lookupVal, setLookupVal] = useState("");
  const [lookupStatus, setLookupStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [provider, setProvider] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [protocol, setProtocol] = useState("openai_compatible");
  const [testing, setTesting] = useState(null);
  const [providerOptions, setProviderOptions] = useState({});

  async function fetchModels() {
    try {
      const r = await fetch(`${API}/models`);
      const d = await r.json();
      setModels(d.models || []);
    } catch { setModels([]); }
  }

  useEffect(() => { fetchModels(); }, []);
  useEffect(() => {
    fetch(`${API}/models/providers`).then(r => r.json()).then(d => setProviderOptions(d.providers || {})).catch(() => {});
  }, []);

  async function testModel(key) {
    setTesting(key);
    try {
      const r = await fetch(`${API}/models/${encodeURIComponent(key)}/test`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Connection test failed");
      await fetchModels();
    } catch (e) { setLookupStatus(`Error: ${e.message}`); }
    finally { setTesting(null); }
  }

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
        body: JSON.stringify({ name_or_url: lookupVal.trim(),
          ...(provider && { provider }), ...(apiBase && { api_base: apiBase }),
          ...(apiKeyEnv && { api_key_env: apiKeyEnv }),
          ...(provider === "custom" && { provider: "custom", protocol }),
        }),
      });
      const d = await r.json();
      if (d.success) {
        const validation = d.model.validation || {};
        setLookupStatus(`${validation.ready ? "✓ Ready" : "Saved"}: ${d.model.display_name}. ${validation.message || "Connection not tested"}`);
        setLookupVal("");
        fetchModels();
      } else {
        setLookupStatus(d.detail || "Could not register model. Check its exact ID and provider.");
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
            placeholder="Exact model ID, catalog name, or OpenRouter model URL"
            value={lookupVal}
            onChange={e => setLookupVal(e.target.value)}
            onKeyDown={e => e.key === "Enter" && lookupModel()}
          />
          <select className="model-select" aria-label="Model provider" value={provider} onChange={e => setProvider(e.target.value)}>
            <option value="">Detect provider</option>
            {Object.keys(providerOptions).map(name => <option key={name} value={name}>{name}</option>)}
            <option value="custom">Custom endpoint</option>
          </select>
          {provider === "custom" && <>
            <input aria-label="API base URL" placeholder="https://your-provider.example/v1" value={apiBase} onChange={e => setApiBase(e.target.value)} />
            <input aria-label="API key environment variable name" placeholder="CUSTOM_API_KEY (variable name only)" value={apiKeyEnv} onChange={e => setApiKeyEnv(e.target.value)} />
            <select aria-label="API protocol" value={protocol} onChange={e => setProtocol(e.target.value)}>
              <option value="openai_compatible">OpenAI compatible</option>
              <option value="anthropic">Anthropic Messages</option>
              <option value="google">Google Generate Content</option>
            </select>
          </>}
          <p style={{ fontSize: 12, color: "var(--text-muted)" }}>Keys stay in your local .env file. Edits apply on the next request. Adding or testing a model makes small provider requests.</p>
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
              <div className="model-card-meta">{m.provider} · {(m.context_window / 1000).toFixed(0)}k ctx · {m.validation?.status || "untested"}</div>
              <div className="model-card-meta">{m.configured ? "Credential configured" : `Set ${m.api_key_env || "provider credentials"} in .env`}</div>
              {m.validation?.message && <div className="model-card-meta">{m.validation.message}</div>}
              <div className="model-card-tags">
                {(m.strengths || []).slice(0, 3).map(s => (
                  <span key={s} className="tag">{s.replace("_", " ")}</span>
                ))}
              </div>
            </div>
            <div className="model-card-actions">
              <button className="primary-btn" disabled={testing !== null} onClick={() => testModel(m.key)}>{testing === m.key ? "Testing…" : "Test"}</button>
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
