import { useEffect, useState } from "react";
import { api, openExternal } from "../api";

export default function ClaudeConnection({ connection, refresh, onUse }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [login, setLogin] = useState(null);
  const [code, setCode] = useState("");
  const [openedLogin, setOpenedLogin] = useState(null);
  useEffect(() => { setLogin(connection?.login || null); }, [connection?.login]);
  useEffect(() => {
    if (!login) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [login, refresh]);
  useEffect(() => {
    if (login?.authUrl && login.loginId !== openedLogin) {
      setOpenedLogin(login.loginId);
      openExternal(login.authUrl).catch(e => setError(e.message));
    }
  }, [login?.authUrl, login?.loginId, openedLogin]);
  async function action(name, body) {
    setBusy(true); setError("");
    try {
      const result = await api(`/connections/claude/${name}`, { method: "POST", body });
      if (name === "login") { setLogin(result); setOpenedLogin(null); }
      if (name === "login/code") setCode("");
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section className="connection-card featured claude-connection">
    <div className="connection-card-top"><div className="provider-mark claude-mark">✳</div><div><h2>Claude Code</h2><p>Use your Claude subscription through Anthropic’s official runtime</p></div><span className={`connection-status ${connection?.connected ? "connected" : ""}`}>{connection?.connected ? "Connected" : "Not connected"}</span></div>
    {connection?.connected ? <>
      <div className="account-identity"><strong>{connection.account?.email || "Claude account"}</strong><span className="pill">{connection.account?.plan || "Subscription"}</span></div>
      <p>Claude Code tasks use your signed-in Claude plan. Account limits and any extra-usage settings are managed by Anthropic.</p>
      <div className="button-row"><button className="primary-btn" onClick={onUse}>Use Claude in chat →</button><button className="secondary-btn" disabled={busy} onClick={() => action("reconnect")}>Reconnect</button><button className="secondary-btn" disabled={busy || !!connection.active_turns} onClick={() => action("logout")}>Sign out / switch account</button></div>
    </> : <>
      <p>Sign in with an eligible Claude account, including Pro or Max. No Anthropic API key is required for this connection.</p>
      <div className="button-row"><button className="primary-btn" disabled={busy || !connection?.installed || !!login} onClick={() => action("login")}>{busy ? "Connecting…" : "Sign in with Claude"}</button><button className="secondary-btn" disabled={busy} onClick={refresh}>Refresh status</button></div>
    </>}
    {connection && !connection.installed && <div className="notice">Install the Python requirements to enable the bundled Claude Code runtime.</div>}
    {login && <div className="login-panel" role="status"><strong>Finish signing in with Claude</strong><p>{login.authUrl ? "Complete the official sign-in in your browser. This page updates automatically." : "Claude Code is preparing your sign-in link…"}</p>
      <div className="button-row">{login.authUrl && <button className="secondary-btn" onClick={() => openExternal(login.authUrl).catch(e => setError(e.message))}>Open Claude sign-in ↗</button>}<button className="secondary-btn" disabled={busy} onClick={() => action("login/cancel")}>Cancel sign-in</button></div>
      {login.authUrl && <form className="claude-code-form" onSubmit={e => { e.preventDefault(); action("login/code", { code }); }}><label className="field-label">If Claude gives you a verification code, paste it here<input aria-label="Claude verification code" type="password" autoComplete="off" maxLength={4096} value={code} onChange={e => setCode(e.target.value)} /></label><button className="secondary-btn" disabled={busy || !code.trim()}>Submit verification code</button></form>}
    </div>}
    {(error || connection?.message) && <div className="error-banner" role="alert">{error || connection.message}</div>}
    <p className="field-help">Claude Code owns sign-in and token renewal. Asrār uses a private Claude configuration and never switches this connection to API mode automatically. Quota percentages are not exposed by this integration.</p>
  </section>;
}
