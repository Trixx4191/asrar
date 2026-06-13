import { useState, useEffect } from "react";
import Chat from "./components/Chat";
import ModelRegistry from "./components/ModelRegistry";
import TaskHistory from "./components/TaskHistory";
import Settings from "./components/Settings";

const API = "http://127.0.0.1:8000";

const NAV = [
  { id: "chat",     icon: "◈", label: "Chat" },
  { id: "models",   icon: "⬡", label: "Models" },
  { id: "history",  icon: "◷", label: "History" },
  { id: "settings", icon: "◎", label: "Settings" },
];

export default function App() {
  const [view, setView]           = useState("chat");
  const [agentName, setAgentName] = useState("Asrār");
  const [backendOk, setBackendOk] = useState(null);
  const [models, setModels]       = useState([]);
  const [forceModel, setForceModel] = useState(null);

  // Health check
  useEffect(() => {
    fetch(`${API}/health`)
      .then(r => r.ok ? setBackendOk(true) : setBackendOk(false))
      .catch(() => setBackendOk(false));
  }, []);

  // Load model list for selector
  useEffect(() => {
    fetch(`${API}/models`)
      .then(r => r.json())
      .then(d => setModels(d.models || []))
      .catch(() => {});
  }, [view]);

  const isElectron = typeof window.asrar !== "undefined";

  return (
    <div className="app-shell">
      {/* Titlebar */}
      <div className="titlebar">
        <span style={{ fontFamily: "var(--font-ar)", fontSize: 13, color: "var(--gold)", marginRight: 8 }}>أسرار</span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{agentName}</span>

        {/* Model picker */}
        {view === "chat" && models.length > 0 && (
          <select
            style={{
              marginLeft: 20, background: "var(--bg-raised)", border: "1px solid var(--border)",
              borderRadius: 6, padding: "3px 8px", fontSize: 11, color: "var(--text-secondary)",
              fontFamily: "var(--font-mono)", cursor: "pointer",
            }}
            value={forceModel || ""}
            onChange={e => setForceModel(e.target.value || null)}
          >
            <option value="">Auto-select model</option>
            {models.filter(m => m.enabled !== false).map(m => (
              <option key={m.key} value={m.key}>{m.display_name}</option>
            ))}
          </select>
        )}

        {isElectron && (
          <div className="titlebar-controls">
            <button className="titlebar-btn" onClick={() => window.asrar.minimize()}>─</button>
            <button className="titlebar-btn" onClick={() => window.asrar.maximize()}>□</button>
            <button className="titlebar-btn close" onClick={() => window.asrar.close()}>✕</button>
          </div>
        )}
      </div>

      <div className="app-body">
        {/* Sidebar */}
        <div className="sidebar">
          <div className="sidebar-logo">
            <div className="logo-name">{agentName}</div>
            <div className="logo-arabic">أسرار · Secrets</div>
          </div>

          <nav className="sidebar-nav">
            {NAV.map(n => (
              <div
                key={n.id}
                className={`nav-item ${view === n.id ? "active" : ""}`}
                onClick={() => setView(n.id)}
              >
                <span className="nav-icon">{n.icon}</span>
                {n.label}
              </div>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              v0.1.0
            </div>
          </div>
        </div>

        {/* Main */}
        <div className="main-content">
          {view === "chat"     && <Chat forceModel={forceModel} />}
          {view === "models"   && <ModelRegistry />}
          {view === "history"  && <TaskHistory />}
          {view === "settings" && <Settings agentName={agentName} onRename={setAgentName} />}
        </div>
      </div>

      {/* Status bar */}
      <div className="status-bar">
        <div className="status-dot" style={{ background: backendOk === false ? "var(--red)" : backendOk ? "var(--green)" : "var(--yellow)" }} />
        <span>{backendOk === false ? "Backend offline — run: python main.py" : backendOk ? "Backend connected" : "Connecting..."}</span>
        <span style={{ marginLeft: "auto" }}>127.0.0.1:8000</span>
      </div>
    </div>
  );
}
