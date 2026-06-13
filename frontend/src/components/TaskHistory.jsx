import { useState, useEffect } from "react";

const API = "http://127.0.0.1:8000";

export default function TaskHistory() {
  const [tasks, setTasks] = useState([]);

  async function fetchTasks() {
    try {
      const r = await fetch(`${API}/tasks`);
      const d = await r.json();
      setTasks(d.tasks || []);
    } catch { setTasks([]); }
  }

  async function clearTasks() {
    if (!confirm("Clear all task history?")) return;
    await fetch(`${API}/tasks`, { method: "DELETE" });
    fetchTasks();
  }

  useEffect(() => { fetchTasks(); }, []);

  return (
    <div className="panel">
      <div className="panel-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div className="panel-title">Task History</div>
          <div className="panel-subtitle">{tasks.length} logged actions</div>
        </div>
        {tasks.length > 0 && (
          <button className="remove-btn" onClick={clearTasks}>Clear all</button>
        )}
      </div>

      <div className="panel-body">
        {tasks.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: "40px 0" }}>
            No tasks yet. Start chatting with Asrār.
          </div>
        ) : tasks.map((t, i) => (
          <div key={i} className="task-row">
            <div className="task-row-top">
              <div className="task-text">{t.task}</div>
              <span className="task-type-badge">{t.task_type}</span>
            </div>
            <div className="task-row-meta">
              <span>⬡ {t.model}</span>
              {t.tools?.length > 0 && <span>⚙ {t.tools.join(", ")}</span>}
              <span>{t.ts ? new Date(t.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
