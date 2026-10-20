import { useEffect, useState } from "react";

const API = "http://127.0.0.1:8000";

export default function Skills() {
  const [skills, setSkills] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API}/skills`, { signal: controller.signal })
      .then(r => { if (!r.ok) throw new Error("Could not load skills"); return r.json(); })
      .then(d => setSkills(d.skills || []))
      .catch(e => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, []);

  async function inspect(name) {
    try {
      const r = await fetch(`${API}/skills/${encodeURIComponent(name)}`);
      if (!r.ok) throw new Error("Could not load skill");
      setSelected((await r.json()).skill);
      setError("");
    } catch (e) { setError(e.message); }
  }

  return <div className="panel">
    <div className="panel-header">
      <div className="panel-title">Workflow skills</div>
      <div className="panel-subtitle">{skills.length} available · Asrār selects relevant playbooks, or request one with $skill-name in chat.</div>
    </div>
    <div className="panel-body">
      {error && <p role="alert">{error}</p>}
      {skills.map(skill => <div className="model-card" key={skill.name}>
        <div className="model-card-info">
          <div className="model-card-name">${skill.name}</div>
          <div className="model-card-meta">{skill.description}</div>
        </div>
        <button className="primary-btn" onClick={() => inspect(skill.name)}>Read</button>
      </div>)}
      {selected && <section aria-label="Skill instructions">
        <h3>{selected.name}</h3>
        <p>{selected.path}</p>
        <pre style={{ whiteSpace: "pre-wrap", padding: 16, lineHeight: 1.6 }}>{selected.content}</pre>
      </section>}
    </div>
  </div>;
}
