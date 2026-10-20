import { useState, useEffect, useCallback } from "react";
import Chat from "./components/Chat";
import ModelRegistry from "./components/ModelRegistry";
import TaskHistory from "./components/TaskHistory";
import Settings from "./components/Settings";
import Skills from "./components/Skills";
import Connections from "./components/Connections";
import { api } from "./api";

const NAV = [
  { id: "chat", icon: "◈", label: "Workspace" },
  { id: "connections", icon: "◎", label: "Accounts" },
  { id: "models", icon: "⬡", label: "API models" },
  { id: "skills", icon: "◇", label: "Skills" },
  { id: "history", icon: "◷", label: "Activity" },
  { id: "settings", icon: "⚙", label: "Settings" },
];

export default function App() {
  const [view, setView] = useState("chat");
  const [backendOk, setBackendOk] = useState(null);
  const [models, setModels] = useState([]);
  const [codexModels, setCodexModels] = useState([]);
  const [preferences, setPreferences] = useState(null);
  const [connection, setConnection] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forceModel, setForceModel] = useState(() => localStorage.getItem("asrar_force_model") || null);
  const runtime = preferences?.runtime || "codex";
  const refreshConnection = useCallback(async () => {
    try {
      const result = await api("/connections/codex");
      setConnection(result);
      if (result.connected) {
        try { const catalog = await api("/connections/codex/models"); setCodexModels(catalog.models || []); }
        catch (e) { setError(e.message); }
      } else setCodexModels([]);
      return result;
    } catch (e) { setError(e.message); }
  }, []);
  const refreshModels = useCallback(async () => {
    try { const data = await api("/models"); setModels(data.models || []); }
    catch (e) { setError(e.message); }
  }, []);
  const load = useCallback(async () => {
    try {
      await api("/health"); setBackendOk(true);
      const settings = await api("/connections/preferences");
      setPreferences(settings); setError("");
      await Promise.all([refreshModels(), refreshConnection()]);
    } catch (e) { setBackendOk(false); setError(e.message); }
  }, [refreshModels, refreshConnection]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const timer = setInterval(async () => {
      try { await api("/health"); setBackendOk(true); }
      catch { setBackendOk(false); }
    }, 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => { if (view === "models") refreshModels(); if (view === "connections") refreshConnection(); }, [view, refreshModels, refreshConnection]);
  useEffect(() => { localStorage.setItem("asrar_force_model", forceModel || ""); }, [forceModel]);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: light)");
    const apply = () => { document.documentElement.dataset.theme = preferences?.theme === "system" ? (media.matches ? "light" : "dark") : preferences?.theme || "dark"; };
    apply(); media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preferences?.theme]);
  async function saveSettings(changes) {
    const result = await api("/connections/preferences", { method: "PATCH", body: changes });
    setPreferences(result); return result;
  }
  async function changeSettings(changes) {
    try { await saveSettings(changes); setError(""); }
    catch (e) { setError(e.message); }
  }
  const selected = codexModels.find(m => (m.model || m.id) === preferences?.codex_model) || codexModels.find(m => m.isDefault) || codexModels[0];
  const agentName = preferences?.agent_name || "Asrār";
  const isElectron = !!window.asrar;
  return <div className="app-shell">
    <div className="titlebar"><span className="titlebar-wordmark">أسرار</span><span>{agentName}</span><span className="titlebar-caption">Your local AI workspace</span>
      {isElectron && <div className="titlebar-controls"><button className="titlebar-btn" aria-label="Minimize" onClick={() => window.asrar.minimize()}>─</button><button className="titlebar-btn" aria-label="Maximize" onClick={() => window.asrar.maximize()}>□</button><button className="titlebar-btn close" aria-label="Close" onClick={() => window.asrar.close()}>✕</button></div>}
    </div>
    <div className="app-body"><aside className="sidebar"><div className="sidebar-logo"><div className="logo-name">{agentName}<span className="version-tag">WORKSPACE</span></div><div className="logo-arabic">A little clarity. A lot of possibility.</div></div><nav className="sidebar-nav" aria-label="Main navigation">{NAV.map(n => <button key={n.id} className={`nav-item ${view === n.id ? "active" : ""}`} onClick={() => setView(n.id)}><span className="nav-icon">{n.icon}</span><span>{n.label}</span></button>)}</nav><div className="sidebar-footer"><button className="sidebar-account" onClick={() => setView("connections")}><span className={`status-dot ${connection?.connected ? "online" : ""}`} /><span>{connection?.connected ? connection.account?.email || "ChatGPT connected" : "Connect your ChatGPT account"}<small>{connection?.account?.plan ? `${connection.account.plan} · Codex` : "Use your subscription"}</small></span><span>↗</span></button><span className="version-label">Asrār 0.14 · Local first</span></div></aside>
      <main className="main-content">
        {error && <div className="app-error" role="alert"><span>{error}</span><button onClick={load}>Retry connection</button><button aria-label="Dismiss error" onClick={() => setError("")}>✕</button></div>}
        <div className="chat-workspace" style={{ display: view === "chat" ? "flex" : "none" }}>
          <div className="workspace-toolbar"><div><span className="eyebrow">WORKSPACE</span><span className="workspace-path" title={preferences?.workspace}>{preferences?.workspace?.split(/[\\/]/).pop() || "Loading…"}</span></div><div className="toolbar-controls"><select aria-label="Connection" disabled={busy || !preferences} value={runtime} onChange={e => changeSettings({ runtime: e.target.value })}><option value="codex">ChatGPT subscription</option><option value="api">API providers · paid separately</option></select>
          {runtime === "codex" ? <><select aria-label="Codex model" disabled={busy || !connection?.connected} value={preferences?.codex_model || ""} onChange={e => changeSettings({ codex_model: e.target.value || null, reasoning_effort: null })}><option value="">Account default</option>{preferences?.codex_model && !codexModels.some(m => (m.model || m.id) === preferences.codex_model) && <option value={preferences.codex_model}>{preferences.codex_model} (unavailable)</option>}{codexModels.map(m => <option key={m.id} value={m.model || m.id}>{m.displayName || m.model}</option>)}</select><select aria-label="Reasoning effort" disabled={busy || !selected} value={preferences?.reasoning_effort || ""} onChange={e => changeSettings({ reasoning_effort: e.target.value || null })}><option value="">Default effort</option>{(selected?.supportedReasoningEfforts || []).map(e => <option key={e.reasoningEffort} value={e.reasoningEffort}>{e.reasoningEffort}</option>)}</select></> : <select aria-label="API model" disabled={busy} value={forceModel || ""} onChange={e => setForceModel(e.target.value || null)}><option value="">Auto-select API model</option>{models.filter(m => m.enabled !== false).map(m => <option key={m.key} value={m.key}>{m.display_name}</option>)}</select>}
          <button className="access-chip" onClick={() => setView("settings")}>{runtime === "codex" ? preferences?.access_mode === "workspace-write" ? "◇ Workspace access" : "◇ Read only" : "API mode"}</button></div></div>
          {runtime === "codex" && !connection?.connected && <div className="onboarding-strip"><span>Use your ChatGPT plan to work with Codex in Asrār.</span><button className="primary-btn" onClick={() => setView("connections")}>Connect account →</button></div>}
          <Chat forceModel={forceModel} models={models} onForceModelChange={setForceModel} onModelsChanged={refreshModels} preferences={preferences} connection={connection} onConnect={() => setView("connections")} onBusyChange={setBusy} />
        </div>
        {view === "connections" && <Connections connection={connection} refresh={refreshConnection} onUseCodex={async () => { await changeSettings({ runtime: "codex" }); setView("chat"); }} onOpenModels={() => setView("models")} />}
        {view === "models" && <ModelRegistry />}{view === "skills" && <Skills />}{view === "history" && <TaskHistory />}{view === "settings" && <Settings preferences={preferences} onSave={saveSettings} models={codexModels} />}
      </main>
    </div>
    <div className="status-bar"><span className="status-dot" style={{ background: backendOk === false ? "var(--red)" : backendOk ? "var(--green)" : "var(--yellow)" }} /><span>{backendOk === false ? "Backend offline" : backendOk ? "Connected locally" : "Connecting…"}</span><span className="status-runtime">{runtime === "codex" ? "ChatGPT subscription · no API fallback" : "API mode · provider billing applies"}</span></div>
  </div>;
}
