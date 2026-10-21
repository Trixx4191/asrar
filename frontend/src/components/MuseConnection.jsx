import { useEffect, useState } from "react";
import { api, openExternal } from "../api";

export default function MuseConnection({ connection, refresh, onUse }) {
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
    if (login?.authUrl && login.loginId !== opened) {
      setOpened(login.loginId);
      openExternal(login.authUrl).catch(e => setError(e.message));
    }
  }, [login?.authUrl, login?.loginId, opened]);
  async function action(name) {
    setBusy(true); setError("");
    try {
      await api(`/connections/muse/${name}`, { method: "POST" });
      if (name === "login") setOpened(null);
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section className="connection-card featured">
    <div className="connection-card-top"><div className="provider-mark">M</div><div><h2>Meta · Muse Code</h2><p>Use Muse Wonder through Meta’s official coding runtime</p></div><span className={`connection-status ${connection?.connected ? "connected" : ""}`}>{connection?.connected ? "Connected" : "Not connected"}</span></div>
    {connection?.connected && !login ? <><div className="account-identity"><strong>{connection.account?.label || "Meta account"}</strong></div><div className="button-row"><button className="primary-btn" onClick={onUse}>Use Muse in chat →</button><button className="secondary-btn" disabled={busy || !!connection.active_turns} onClick={() => action("reconnect")}>Reconnect</button><button className="secondary-btn" disabled={busy || !!connection.active_turns} onClick={() => action("logout")}>Sign out / switch account</button></div></> : <><p>Sign in with your eligible Muse Code account. Models and usage depend on your subscription.</p><div className="button-row"><button className="primary-btn" disabled={busy || !connection?.installed || !!login} onClick={() => action("login")}>Sign in with Meta</button><button className="secondary-btn" disabled={busy} onClick={refresh}>Refresh status</button></div></>}
    {connection && !connection.installed && <div className="notice">Install the official Muse Code CLI, or set ASRAR_MUSE_BIN to its executable path, then refresh.</div>}
    {login && <div className="login-panel" role="status"><strong>Finish signing in with Meta</strong><p>Open the official verification page and enter this code if asked:</p><div className="device-code">{login.userCode}</div><div className="button-row"><button className="secondary-btn" disabled={!login.authUrl} onClick={() => openExternal(login.authUrl).catch(e => setError(e.message))}>Open Meta sign-in ↗</button><button className="secondary-btn" disabled={busy} onClick={() => action("login/cancel")}>Cancel sign-in</button></div></div>}
    {(error || connection?.message) && <div className="error-banner" role="alert">{error || connection.message}</div>}
    <p className="field-help">Wonder Full requires Power Usage or Maximum Usage. The max reasoning setting is separate from Full. Only models returned for your account appear in chat. Manage Standard / Contributor mode in Muse; Contributor mode may let Meta train on submitted content.</p>
    <p className="field-help">Muse handles sign-in and token renewal in Asrār’s private profile. This connection never falls back to API billing. A Meta API key can be configured separately in API models.</p>
  </section>;
}
