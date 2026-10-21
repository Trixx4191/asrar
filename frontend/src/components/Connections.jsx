import { useEffect, useState } from "react";
import { api, openExternal } from "../api";
import ClaudeConnection from "./ClaudeConnection";
import KimiConnection from "./KimiConnection";
import MuseConnection from "./MuseConnection";

export default function Connections({ connection, refresh, onUseCodex, onOpenModels, claudeConnection, refreshClaude, onUseClaude, kimiConnection, refreshKimi, onUseKimi, museConnection, refreshMuse, onUseMuse }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [login, setLogin] = useState(null);
  const [usage, setUsage] = useState(null);
  const [usageError, setUsageError] = useState("");
  const [copied, setCopied] = useState(false);
  const signedIn = connection?.connected;
  useEffect(() => { setLogin(connection?.login || null); }, [connection?.login]);
  useEffect(() => {
    if (!login) return;
    const timer = setInterval(() => refresh(), 2500);
    return () => clearInterval(timer);
  }, [login, refresh]);
  useEffect(() => {
    if (!signedIn) { setUsage(null); return; }
    loadUsage();
  }, [signedIn]);
  async function loadUsage() {
    setUsageError("");
    try { setUsage(await api("/connections/codex/usage")); }
    catch (e) { setUsageError(e.message); }
  }
  async function action(kind, body) {
    setBusy(true); setError("");
    try {
      const result = await api(`/connections/codex/${kind}`, { method: "POST", body });
      if (kind === "login") {
        setLogin(result);
        if (result.authUrl) await openExternal(result.authUrl);
      } else setLogin(null);
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  const buckets = usage?.rateLimitsByLimitId || (usage?.rateLimits ? { codex: usage.rateLimits } : {});
  return <div className="panel connections-page">
    <div className="panel-header"><div className="eyebrow">YOUR AI, CONNECTED</div><h1>Accounts & connections</h1><p>Connect your ChatGPT, Claude, Kimi, or Meta account. Keep your work in Asrār.</p></div>
    <div className="panel-body">
      <section className="connection-card featured">
        <div className="connection-card-top"><div className="provider-mark">◎</div><div><h2>ChatGPT</h2><p>Powered by the official Codex runtime</p></div><span className={`connection-status ${signedIn ? "connected" : ""}`}>{signedIn ? "Connected" : "Not connected"}</span></div>
        {signedIn ? <>
          <div className="account-identity"><strong>{connection.account?.email || "ChatGPT account"}</strong><span className="pill">{connection.account?.plan || "Subscription"}</span></div>
          <p>Your tasks use this account’s Codex allowance. Models and limits depend on your plan.</p>
          <div className="button-row"><button className="primary-btn" onClick={onUseCodex}>Use in chat →</button><button className="secondary-btn" disabled={busy} onClick={() => action("reconnect")}>Reconnect</button><button className="secondary-btn" disabled={busy || !!connection.active_turns} onClick={() => action("logout")}>Sign out / switch account</button></div>
        </> : <>
          <p>Sign in with your eligible ChatGPT account, including Plus. No OpenAI API key is needed for Codex tasks.</p>
          <div className="button-row"><button className="primary-btn" disabled={busy || !connection?.installed || !!login} onClick={() => action("login", { method: "browser" })}>{busy ? "Connecting…" : "Sign in with ChatGPT"}</button><button className="secondary-btn" disabled={busy || !connection?.installed || !!login} onClick={() => action("login", { method: "device" })}>Use a device code</button><button className="secondary-btn" disabled={busy} onClick={refresh}>Refresh status</button></div>
        </>}
        {connection && !connection.installed && <div className="notice">Install the bundled runtime: run <code>npm install</code> in the frontend folder, then refresh.</div>}
        {login && <div className="login-panel" role="status"><strong>Finish signing in with OpenAI</strong><p>{login.userCode ? "Open the verification page and enter this code:" : "Complete sign-in in your browser. This page updates automatically."}</p>
          {login.userCode && <button className="device-code" onClick={async () => { try { await navigator.clipboard.writeText(login.userCode); setCopied(true); } catch { setError("Copy the code shown here manually."); } }}>{login.userCode} <small>{copied ? "Copied" : "Copy"}</small></button>}
          <div className="button-row"><button className="secondary-btn" onClick={() => openExternal(login.authUrl || login.verificationUrl).catch(e => setError(e.message))}>Open sign-in page ↗</button><button className="secondary-btn" disabled={busy} onClick={() => action("login/cancel")}>Cancel sign-in</button></div>
        </div>}
        {(error || connection?.message) && <div className="error-banner" role="alert">{error || connection.message}</div>}
      </section>
      {signedIn && <section className="connection-card"><div className="section-heading"><h2>Subscription usage</h2><button className="secondary-btn" onClick={loadUsage}>Refresh</button></div>
        {Object.entries(buckets).map(([key, bucket]) => <div key={key} className="usage-bucket"><h3>{bucket.limitName || "Codex"}</h3>{["primary", "secondary"].filter(k => bucket[k]).map((name, index) => {
          const quota = bucket[name]; const percent = Math.min(100, Math.max(0, quota.usedPercent || 0));
          return <div key={name} className="usage-window"><div><span>{index === 0 ? "Current window" : "Extended window"}</span><strong>{Math.round(percent)}% used</strong></div><progress max="100" value={percent} /><small>{quota.resetsAt ? `Resets ${new Date(quota.resetsAt * 1000).toLocaleString()}` : "Reset time unavailable"}</small></div>;
        })}</div>)}
        {!Object.keys(buckets).length && <p>{usageError || "Usage details are not available yet."}</p>}
        {usageError && Object.keys(buckets).length > 0 && <div className="notice">{usageError}</div>}
      </section>}
      <ClaudeConnection connection={claudeConnection} refresh={refreshClaude} onUse={onUseClaude} />
      <MuseConnection connection={museConnection} refresh={refreshMuse} onUse={onUseMuse} />
      <KimiConnection connection={kimiConnection} refresh={refreshKimi} onUse={onUseKimi} />
      <section className="connection-card"><div className="connection-card-top"><div className="provider-mark">Q</div><div><h2>Qwen Code</h2><p>Account sign-in unavailable</p></div></div><p>Qwen’s official documentation says account OAuth sign-in was discontinued on April 15, 2026. Your Qwen API connection is still available in API mode.</p><div className="button-row"><button className="secondary-btn" onClick={onOpenModels}>Manage Qwen API connection →</button><button className="secondary-btn" onClick={() => openExternal("https://qwenlm.github.io/qwen-code-docs/en/users/support/troubleshooting/").catch(e => setError(e.message))}>Official sign-in notice ↗</button></div></section>
      <section className="connection-card"><div className="connection-card-top"><div className="provider-mark muted">⌘</div><div><h2>Other model providers</h2><p>Optional API connections</p></div><span className="pill">Separate billing</span></div><p>Your existing providers are still available in API mode. Account connections use Codex, Claude Code, Kimi Code, or Muse Code. API mode uses your configured provider keys.</p><button className="secondary-btn" onClick={onOpenModels}>Manage API models →</button></section>
      <div className="privacy-note">Sign-in and token renewal are handled by each official runtime. Asrār keeps its own local sign-in, separate from VS Code. Signing out here does not sign you out of VS Code. Subscription tasks never switch to paid API calls automatically.</div>
    </div>
  </div>;
}
