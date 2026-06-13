import { useState } from "react";

const DEFAULTS = {
  auto_select_model: true,
  fallback_enabled: true,
  explain_routing: true,
  confirm_shell_cmds: true,
  safe_mode: true,
  stream_responses: true,
};

function Toggle({ on, onToggle }) {
  return <div className={`toggle-switch ${on ? "on" : ""}`} onClick={onToggle} />;
}

export default function Settings({ agentName, onRename }) {
  const [settings, setSettings] = useState(() => {
    try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("asrar_settings") || "{}") }; }
    catch { return DEFAULTS; }
  });
  const [nameInput, setNameInput] = useState(agentName || "Asrār");
  const [saved, setSaved] = useState(false);

  function toggle(key) {
    const next = { ...settings, [key]: !settings[key] };
    setSettings(next);
    localStorage.setItem("asrar_settings", JSON.stringify(next));
  }

  function saveIdentity() {
    onRename?.(nameInput);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  const rows = [
    { key: "auto_select_model",   label: "Auto-select model",    desc: "Agent picks the best model per task" },
    { key: "fallback_enabled",    label: "Fallback chain",        desc: "Try next model if primary fails" },
    { key: "explain_routing",     label: "Show routing reason",   desc: "Display which model was chosen and why" },
    { key: "confirm_shell_cmds",  label: "Confirm shell commands",desc: "Ask before running any terminal command" },
    { key: "safe_mode",           label: "Safe mode",             desc: "Block destructive commands entirely" },
    { key: "stream_responses",    label: "Stream responses",      desc: "Show output token by token" },
  ];

  return (
    <div className="panel">
      <div className="panel-header">
        <div className="panel-title">Settings</div>
        <div className="panel-subtitle">Agent behavior & identity</div>
      </div>

      <div className="panel-body">
        {/* Identity */}
        <div className="settings-group">
          <div className="settings-row">
            <div>
              <div className="settings-row-label">Agent name</div>
              <div className="settings-row-desc">Shown in the UI and logs</div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                style={{
                  background: "var(--bg-base)", border: "1px solid var(--border)",
                  borderRadius: 6, padding: "6px 10px", fontSize: 13,
                  color: "var(--text-primary)", width: 140,
                }}
                value={nameInput}
                onChange={e => setNameInput(e.target.value)}
              />
              <button className="primary-btn" onClick={saveIdentity}>
                {saved ? "Saved ✓" : "Save"}
              </button>
            </div>
          </div>
        </div>

        {/* Behavior toggles */}
        <div className="settings-group">
          {rows.map(r => (
            <div key={r.key} className="settings-row">
              <div>
                <div className="settings-row-label">{r.label}</div>
                <div className="settings-row-desc">{r.desc}</div>
              </div>
              <Toggle on={settings[r.key]} onToggle={() => toggle(r.key)} />
            </div>
          ))}
        </div>

        {/* API status */}
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.7 }}>
          API keys are loaded from <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>.env</code> in the project root.
          Edit that file and restart the backend to update keys.
        </div>
      </div>
    </div>
  );
}
