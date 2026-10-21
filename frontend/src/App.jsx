import { useState, useEffect, useCallback } from "react";
import Chat from "./components/Chat";
import ModelRegistry from "./components/ModelRegistry";
import TaskHistory from "./components/TaskHistory";
import Settings from "./components/Settings";
import Skills from "./components/Skills";
import Connections from "./components/Connections";
import { api } from "./api";
import { readWallpapers, importWallpaper, deleteWallpaper } from "./wallpaperStore";
import { DEFAULT_APPEARANCE, normalizeAppearance, themeStyle } from "./appearance";
import lilyWallpaper from "./assets/wallpapers/4tyfpm.jpeg";
import floralWallpaper from "./assets/wallpapers/4bfb4.jpeg";
import portraitWallpaper from "./assets/wallpapers/424.jpeg";

const WALLPAPERS = [
  { id: "lilies", name: "Midnight lilies", image: lilyWallpaper },
  { id: "floral", name: "Ivory bloom", image: floralWallpaper },
  { id: "portrait", name: "Lace portrait", image: portraitWallpaper },
];

function loadAppearance() {
  try { return normalizeAppearance(JSON.parse(localStorage.getItem("asrar_appearance") || "{}")); }
  catch { return { ...DEFAULT_APPEARANCE }; }
}

const NAV = [
  { id: "chat", icon: "◈", label: "Workspace" },
  { id: "connections", icon: "◎", label: "Accounts" },
  { id: "models", icon: "⬡", label: "API models" },
  { id: "skills", icon: "◇", label: "Skills" },
  { id: "history", icon: "◷", label: "Activity" },
  { id: "settings", icon: "⚙", label: "Settings" },
];

function SubscriptionControls({ runtime, models, preferences, connected, busy, onChange }) {
  const modelKey = `${runtime}_model`;
  const effortKey = runtime === "claude" ? "claude_effort" : "reasoning_effort";
  const current = preferences?.[modelKey];
  const selected = models.find(m => (m.model || m.id) === current) || models.find(m => m.isDefault);
  const label = runtime === "kimi" ? "Kimi" : runtime === "claude" ? "Claude" : "Codex";
  return <>
    <select aria-label={`${label} model`} disabled={busy || !connected} value={current || ""}
      onChange={e => onChange({ [modelKey]: e.target.value || null, ...(runtime === "kimi" ? {} : { [effortKey]: null }) })}>
      <option value="">Account default</option>
      {current && !models.some(m => (m.model || m.id) === current) && <option value={current}>{current} (unavailable)</option>}
      {models.map(m => <option key={m.id} value={m.model || m.id}>{m.displayName || m.model}</option>)}
    </select>
    {runtime !== "kimi" && <select aria-label={`${label} reasoning effort`} disabled={busy || !selected || !connected}
      value={preferences?.[effortKey] || ""} onChange={e => onChange({ [effortKey]: e.target.value || null })}>
      <option value="">Default effort</option>
      {(selected?.supportedReasoningEfforts || []).map(level => <option key={level.reasoningEffort} value={level.reasoningEffort}>{level.reasoningEffort}</option>)}
    </select>}
  </>;
}

export default function App() {
  const [resolvedTheme, setResolvedTheme] = useState("dark");
  const [view, setView] = useState("chat");
  const [appearance, setAppearance] = useState(loadAppearance);
  useEffect(() => { localStorage.setItem("asrar_appearance", JSON.stringify(appearance)); }, [appearance]);
  const [wallpaper, setWallpaper] = useState(() => localStorage.getItem("asrar_wallpaper") || "lilies");
  useEffect(() => { localStorage.setItem("asrar_wallpaper", wallpaper); }, [wallpaper]);
  const [uploadedWallpapers, setUploadedWallpapers] = useState([]);
  const [wallpaperStorageError, setWallpaperStorageError] = useState("");
  const wallpapers = [...WALLPAPERS, ...uploadedWallpapers];
  useEffect(() => {
    let active = true;
    readWallpapers().then(images => { if (active) setUploadedWallpapers(images); })
      .catch(e => { if (active) setWallpaperStorageError(e.message); });
    return () => { active = false; };
  }, []);
  async function uploadWallpaper(file) {
    const record = await importWallpaper(file);
    setUploadedWallpapers(old => [...old, record]); setWallpaper(record.id);
    setWallpaperStorageError("");
  }
  async function removeWallpaper(id) {
    await deleteWallpaper(id);
    setUploadedWallpapers(old => old.filter(image => image.id !== id));
    setWallpaper(old => old === id ? "lilies" : old);
  }
  const [backendOk, setBackendOk] = useState(null);
  const [models, setModels] = useState([]);
  const [codexModels, setCodexModels] = useState([]);
  const [claudeModels, setClaudeModels] = useState([]);
  const [kimiModels, setKimiModels] = useState([]);
  const [kimiConnection, setKimiConnection] = useState(null);
  const [preferences, setPreferences] = useState(null);
  const [connection, setConnection] = useState(null);
  const [claudeConnection, setClaudeConnection] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forceModel, setForceModel] = useState(() => localStorage.getItem("asrar_force_model") || null);
  const runtime = preferences?.runtime || "codex";

  const refreshConnection = useCallback(async () => {
    try {
      const result = await api("/connections/codex");
      setConnection(result);
      if (result.connected && !result.login) {
        const catalog = await api("/connections/codex/models");
        setCodexModels(catalog.models || []);
      } else setCodexModels([]);
      return result;
    } catch (e) { setError(e.message); }
  }, []);
  const refreshClaude = useCallback(async () => {
    try {
      const result = await api("/connections/claude");
      setClaudeConnection(result);
      if (result.connected && !result.login) {
        const catalog = await api("/connections/claude/models");
        setClaudeModels(catalog.models || []);
      } else setClaudeModels([]);
      return result;
    } catch (e) { setError(e.message); }
  }, []);
  const refreshKimi = useCallback(async () => {
    try {
      const result = await api("/connections/kimi");
      setKimiConnection(result);
      const catalog = result.connected ? await api("/connections/kimi/models") : { models: [] };
      setKimiModels(catalog.models || []);
      return result;
    } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => {
    if (runtime === "kimi" && !busy) refreshKimi();
  }, [runtime, busy, refreshKimi]);
  const refreshModels = useCallback(async () => {
    try { const data = await api("/models"); setModels(data.models || []); }
    catch (e) { setError(e.message); }
  }, []);
  const load = useCallback(async () => {
    try {
      await api("/health"); setBackendOk(true);
      const settings = await api("/connections/preferences");
      setPreferences(settings); setError("");
      await Promise.all([refreshModels(), refreshConnection(), refreshClaude(), refreshKimi()]);
    } catch (e) { setBackendOk(false); setError(e.message); }
  }, [refreshModels, refreshConnection, refreshClaude, refreshKimi]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const timer = setInterval(async () => {
      try { await api("/health"); setBackendOk(true); }
      catch { setBackendOk(false); }
    }, 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (view === "models") refreshModels();
    if (view === "connections") { refreshConnection(); refreshClaude(); refreshKimi(); }
  }, [view, refreshModels, refreshConnection, refreshClaude, refreshKimi]);
  useEffect(() => { localStorage.setItem("asrar_force_model", forceModel || ""); }, [forceModel]);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: light)");
    const apply = () => {
      const choice = appearance.mode || preferences?.theme || "dark";
      const resolved = choice === "system" ? (media.matches ? "light" : "dark") : choice;
      document.documentElement.dataset.theme = resolved; setResolvedTheme(resolved);
    };
    apply(); media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preferences?.theme, appearance.mode]);

  async function saveSettings(changes) {
    const result = await api("/connections/preferences", { method: "PATCH", body: changes });
    setPreferences(result); return result;
  }
  async function changeSettings(changes) {
    try { await saveSettings(changes); setError(""); return true; }
    catch (e) { setError(e.message); return false; }
  }
  async function useRuntime(value) {
    if (await changeSettings({ runtime: value })) setView("chat");
  }
  const selectedConnection = runtime === "kimi" ? kimiConnection : runtime === "claude" ? claudeConnection : runtime === "codex" ? connection : null;
  const subscriptionName = runtime === "kimi" ? "Kimi" : runtime === "claude" ? "Claude" : "ChatGPT";
  const agentName = preferences?.agent_name || "Asrār";
  const isElectron = !!window.asrar;

  return <div className="app-shell" data-wallpaper={wallpaper} style={{ ...themeStyle(appearance, resolvedTheme), "--wallpaper": `url("${(wallpapers.find(item => item.id === wallpaper) || WALLPAPERS[0]).image}")`,
    "--glass-alpha": 1 - appearance.transparency / 100,
    "--surface-alpha": Math.min(1, 1 - appearance.transparency / 100 + .10),
    "--strong-alpha": Math.min(1, 1 - appearance.transparency / 100 + .24),
    "--glass-blur": `${appearance.blur}px`, "--wallpaper-dim": appearance.dim / 100 }}>
    <div className="workspace-wallpaper" aria-hidden="true" />
    <div className="titlebar">
      <span className="titlebar-wordmark">أسرار</span><span>{agentName}</span><span className="titlebar-caption">Your local AI workspace</span>
      {isElectron && <div className="titlebar-controls">
        <button className="titlebar-btn" aria-label="Minimize" onClick={() => window.asrar.minimize()}>─</button>
        <button className="titlebar-btn" aria-label="Maximize" onClick={() => window.asrar.maximize()}>□</button>
        <button className="titlebar-btn close" aria-label="Close" onClick={() => window.asrar.close()}>✕</button>
      </div>}
    </div>
    <div className="app-body">
      <aside className="sidebar">
        <div className="sidebar-logo"><div className="logo-name">{agentName}<span className="version-tag">WORKSPACE</span></div><div className="logo-arabic">A little clarity. A lot of possibility.</div></div>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {NAV.map(n => <button key={n.id} title={n.label} aria-current={view === n.id ? "page" : undefined} className={`nav-item ${view === n.id ? "active" : ""}`} onClick={() => setView(n.id)}><span className="nav-icon">{n.icon}</span><span>{n.label}</span></button>)}
        </nav>
        <div className="sidebar-footer">
          <div className="wallpaper-picker" role="group" aria-label="Workspace wallpaper">
            <span className="eyebrow">YOUR ATMOSPHERE</span>
            <div>{wallpapers.map(item => <button key={item.id} className={`wallpaper-swatch ${wallpaper === item.id ? "selected" : ""}`} style={{ backgroundImage: `url("${item.image}")` }} title={item.name} aria-label={item.name} aria-pressed={wallpaper === item.id} onClick={() => setWallpaper(item.id)}>{wallpaper === item.id && <span>✓</span>}</button>)}</div>
          </div>
          <button className="sidebar-account" onClick={() => setView("connections")}>
            <span className={`status-dot ${selectedConnection?.connected ? "online" : ""}`} />
            <span>{selectedConnection?.connected ? selectedConnection.account?.email || `${subscriptionName} connected` : runtime === "api" ? "Accounts & providers" : `Connect your ${subscriptionName} account`}
              <small>{selectedConnection?.account?.plan || (runtime === "api" ? "Manage connections" : "Use your subscription")}</small>
            </span><span>↗</span>
          </button>
          <span className="version-label">Asrār 0.14 · Local first</span>
        </div>
      </aside>
      <main className="main-content">
        {error && <div className="app-error" role="alert"><span>{error}</span><button onClick={load}>Retry connection</button><button aria-label="Dismiss error" onClick={() => setError("")}>✕</button></div>}
        <div className="chat-workspace" style={{ display: view === "chat" ? "flex" : "none" }}>
          <div className="workspace-toolbar">
            <div><span className="eyebrow">WORKSPACE</span><span className="workspace-path" title={preferences?.workspace}>{preferences?.workspace?.split(/[\\/]/).pop() || "Loading…"}</span></div>
            <div className="toolbar-controls">
              <select aria-label="Connection" disabled={busy || !preferences} value={runtime} onChange={e => changeSettings({ runtime: e.target.value })}>
                <option value="codex">ChatGPT subscription</option><option value="claude">Claude subscription</option><option value="kimi">Kimi account</option><option value="api">API providers · paid separately</option>
              </select>
              {runtime !== "api" ? <SubscriptionControls runtime={runtime} models={runtime === "kimi" ? kimiModels : runtime === "claude" ? claudeModels : codexModels} preferences={preferences} connected={selectedConnection?.connected} busy={busy} onChange={changeSettings} /> :
                <select aria-label="API model" disabled={busy} value={forceModel || ""} onChange={e => setForceModel(e.target.value || null)}>
                  <option value="">Auto-select API model</option>{models.filter(m => m.enabled !== false).map(m => <option key={m.key} value={m.key}>{m.display_name}</option>)}
                </select>}
              <button className="access-chip" onClick={() => setView("settings")}>{runtime === "kimi" ? "◇ Runtime permissions" : runtime !== "api" ? preferences?.access_mode === "workspace-write" ? "◇ Workspace access" : "◇ Read only" : "API mode"}</button>
            </div>
          </div>
          {runtime !== "api" && !selectedConnection?.connected && <div className="onboarding-strip"><span>Use your {subscriptionName} plan to work with {runtime === "kimi" ? "Kimi Code" : runtime === "claude" ? "Claude Code" : "Codex"} in Asrār.</span><button className="primary-btn" onClick={() => setView("connections")}>Connect account →</button></div>}
          <Chat forceModel={forceModel} models={models} onForceModelChange={setForceModel} onModelsChanged={refreshModels} preferences={preferences} connection={selectedConnection} onConnect={() => setView("connections")} onBusyChange={setBusy} />
        </div>
        {view === "connections" && <Connections connection={connection} refresh={refreshConnection} onUseCodex={() => useRuntime("codex")} onOpenModels={() => setView("models")} claudeConnection={claudeConnection} refreshClaude={refreshClaude} onUseClaude={() => useRuntime("claude")} kimiConnection={kimiConnection} refreshKimi={refreshKimi} onUseKimi={() => useRuntime("kimi")} />}
        {view === "models" && <ModelRegistry />}
        {view === "skills" && <Skills />}
        {view === "history" && <TaskHistory />}
        {view === "settings" && <Settings resolvedTheme={resolvedTheme} appearance={appearance} onAppearanceChange={changes => setAppearance(old => ({ ...old, ...changes }))} onAppearanceReset={() => { setAppearance(DEFAULT_APPEARANCE); setWallpaper("lilies"); }} wallpaperStorageError={wallpaperStorageError} onWallpaperUpload={uploadWallpaper} onWallpaperRemove={removeWallpaper} wallpapers={wallpapers} wallpaper={wallpaper} onWallpaperChange={setWallpaper} preferences={preferences} onSave={saveSettings} models={codexModels} claudeModels={claudeModels} />}
      </main>
    </div>
    <div className="status-bar"><span className="status-dot" style={{ background: backendOk === false ? "var(--red)" : backendOk ? "var(--green)" : "var(--yellow)" }} /><span>{backendOk === false ? "Backend offline" : backendOk ? "Connected locally" : "Connecting…"}</span><span className="status-runtime">{runtime !== "api" ? `${subscriptionName} subscription · no API fallback` : "API mode · provider billing applies"}</span></div>
  </div>;
}
