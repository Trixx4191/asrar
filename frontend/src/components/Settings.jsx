import { useEffect, useState } from "react";
import Appearance from "./Appearance";

export default function Settings({ preferences, onSave, models = [], claudeModels = [], museModels = [], ...appearanceProps }) {
  const [draft, setDraft] = useState(preferences);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { setDraft(preferences); }, [preferences]);
  if (!draft) return <div className="panel"><div className="panel-header"><h1>Settings</h1></div><div className="panel-body"><Appearance {...appearanceProps} preferredMode={preferences?.theme} /><p>Connecting to workspace settings…</p></div></div>;
  const change = (key, value) => { setDraft(old => ({ ...old, [key]: value })); setMessage(""); };
  async function save(e) {
    e.preventDefault(); setBusy(true); setError("");
    try { await onSave(draft); setMessage("Settings saved. Workspace and access changes apply to the next task."); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function browse() {
    const result = await window.asrar?.chooseWorkspace?.();
    if (result) change("workspace", result);
  }
  return <div className="panel settings-page"><div className="panel-header"><div className="eyebrow">MAKE IT YOURS</div><h1>Settings</h1><p>Your workspace, your preferred models, your controls.</p></div><form className="panel-body" onSubmit={save}>
    <Appearance {...appearanceProps} preferredMode={preferences?.theme} />
    <section className="connection-card"><h2>Workspace & access</h2><p>Subscription tasks run in the selected folder. Start a new chat after changing workspaces.</p>
      <label className="field-label">Workspace folder<div className="inline-field"><input value={draft.workspace} onChange={e => change("workspace", e.target.value)} required />{window.asrar?.chooseWorkspace && <button type="button" className="secondary-btn" onClick={browse}>Browse…</button>}</div></label>
      <label className="field-label">Access mode<select value={draft.access_mode} onChange={e => change("access_mode", e.target.value)}><option value="read-only">Read only — inspect and explain</option><option value="workspace-write">Workspace — edit files and run tools</option></select></label>
      <p className="field-help">Codex and Claude read and search files, edit workspace files, and run sandboxed commands automatically in Workspace mode. Destructive actions and access beyond the sandbox require approval. Managed runtime policies can restrict access further. Kimi reads and searches automatically and uses native approvals for other tools rather than an operating system sandbox; read-only denies edit and command permission requests. Network and search switches are instructions for Kimi, not enforced network isolation.</p>
      <label className="check-row"><input type="checkbox" checked={draft.network_access} disabled={draft.access_mode === "read-only"} onChange={e => change("network_access", e.target.checked)} /><span>Allow network access inside workspace tasks<small>Read-only mode keeps network access restricted.</small></span></label>
    </section>
    <section className="connection-card"><h2>Models & instructions</h2><label className="field-label">Default connection<select value={draft.runtime} onChange={e => change("runtime", e.target.value)}><option value="codex">ChatGPT subscription · Codex</option><option value="claude">Claude subscription · Claude Code</option><option value="muse">Meta account · Muse Code</option><option value="kimi">Kimi account · Kimi Code</option><option value="api">API providers · separate billing</option></select></label>
      <label className="field-label">Default Codex model<select value={draft.codex_model || ""} onChange={e => { change("codex_model", e.target.value || null); change("reasoning_effort", null); }}><option value="">Account default</option>{draft.codex_model && !models.some(m => (m.model || m.id) === draft.codex_model) && <option value={draft.codex_model}>{draft.codex_model} (reconnect to check)</option>}{models.map(m => <option key={m.id} value={m.model || m.id}>{m.displayName || m.model}</option>)}</select></label>
      <label className="field-label">Default Muse model<select value={draft.muse_model || ""} onChange={e => { change("muse_model", e.target.value || null); change("muse_effort", null); }}><option value="">Account default</option>{draft.muse_model && !museModels.some(m => m.id === draft.muse_model) && <option value={draft.muse_model}>{draft.muse_model} (refresh account to check)</option>}{museModels.map(m => <option key={m.id} value={m.id}>{m.displayName}</option>)}</select></label>
      <label className="field-label">Default Claude model<select value={draft.claude_model || ""} onChange={e => { change("claude_model", e.target.value || null); change("claude_effort", null); }}><option value="">Account default</option>{draft.claude_model && !claudeModels.some(m => m.id === draft.claude_model) && <option value={draft.claude_model}>{draft.claude_model} (reconnect to check)</option>}{claudeModels.map(m => <option key={m.id} value={m.id}>{m.displayName}</option>)}</select></label>
      <label className="field-label">Web search<select value={draft.web_search || "disabled"} onChange={e => change("web_search", e.target.value)}><option value="disabled">Disabled</option><option value="cached">Cached search results</option><option value="live">Live web search</option></select></label><p className="field-help">Search is provided by the selected subscription runtime, subject to account policies. Claude Code treats cached/live as enabled search. This is separate from terminal network access.</p>
      <label className="field-label">Custom instructions<textarea rows="5" maxLength={12000} placeholder="How should Asrār approach your work?" value={draft.instructions} onChange={e => change("instructions", e.target.value)} /></label><p className="field-help">Applies to Codex and Claude Code conversations on the next task. Avoid including passwords or API keys.</p>
    </section>
    <section className="connection-card"><h2>Appearance & interaction</h2><label className="field-label">Assistant name<input value={draft.agent_name} maxLength={40} required onChange={e => change("agent_name", e.target.value)} /></label><label className="check-row"><input type="checkbox" checked={draft.show_activity} onChange={e => change("show_activity", e.target.checked)} /><span>Show task activity, routing, and timing</span></label><label className="check-row"><input type="checkbox" checked={draft.enter_to_send} onChange={e => change("enter_to_send", e.target.checked)} /><span>Enter sends messages<small>Shift+Enter adds a new line. Ctrl/Cmd+Enter always sends.</small></span></label></section>
    <div className="settings-save"><button className="primary-btn" disabled={busy}>{busy ? "Saving…" : "Save settings"}</button><span role="status">{message}</span></div>{error && <div className="error-banner" role="alert">{error}</div>}
  </form></div>;
}
