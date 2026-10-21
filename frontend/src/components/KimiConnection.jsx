import { useEffect, useState } from "react";
import { api, openExternal } from "../api";

export default function KimiConnection({ connection, refresh, onUse }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(null);
  const login = connection?.login;
  useEffect(() => {
    if (!login) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [!!login, refresh]);
  useEffect(() => {
    if (login?.authUrl && login.authUrl !== opened) {
      setOpened(login.authUrl);
      openExternal(login.authUrl).catch(e => setError(e.message));
    }
  }, [login?.authUrl, opened]);
  async function action(name) {
    setBusy(true); setError("");
    try {
      await api(`/connections/kimi/${name}`, { method: "POST" });
      if (name === "login") setOpened(null);
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section className="connection-card featured">
    <div className="connection-card-top"><div className="provider-mark">K</div><div><h2>Kimi Code</h2><p>Sign in with your Kimi account through the official CLI</p></div><span className={`connection-status ${connection?.connected ? "connected" : ""}`}>{connection?.connected ? "Connected" : "Not connected"}</span></div>
    <p>Use an eligible Kimi Code account. Your plan’s allowance applies; no API key is required.</p>
    <div className="button-row">
      {connection?.connected && !login ? <><button className="primary-btn" onClick={onUse}>Use Kimi in chat →</button><button className="secondary-btn" disabled={busy || !!connection.active_turns} onClick={() => action("logout")}>Sign out / switch account</button></> : <button className="primary-btn" disabled={busy || !connection?.installed || !!login} onClick={() => action("login")}>Sign in with Kimi</button>}
      <button className="secondary-btn" disabled={busy} onClick={refresh}>Refresh status</button>
    </div>
    {connection && !connection.installed && <div className="notice">Install the Python Kimi Code CLI with <code>uv tool install kimi-cli</code>, then refresh. A custom executable can be set with <code>ASRAR_KIMI_BIN</code>.</div>}
    {login && <div className="login-panel" role="status"><strong>Finish signing in with Kimi</strong><p>{login.authUrl ? "Complete account authorization in your browser. This page updates automatically." : "Kimi is preparing your sign-in link…"}</p><div className="button-row">{login.authUrl && <button className="secondary-btn" onClick={() => openExternal(login.authUrl).catch(e => setError(e.message))}>Open Kimi sign-in ↗</button>}<button className="secondary-btn" disabled={busy} onClick={() => action("login/cancel")}>Cancel sign-in</button></div></div>}
    {(error || connection?.message) && <div className="error-banner" role="alert">{error || connection.message}</div>}
    <p className="field-help">Kimi owns sign-in and token renewal. Asrār keeps this account separate from your terminal login. Task permissions use Kimi’s approval system; an operating system sandbox is not provided. API connections remain available separately.</p>
  </section>;
}
