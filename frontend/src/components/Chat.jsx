import { useState, useRef, useEffect } from "react";

const API = "http://127.0.0.1:8000";

function ModelBadge({ model, taskType }) {
  if (!model) return null;
  return (
    <span className="model-badge">
      <span className="dot" />
      {model}
      {taskType && <span style={{ opacity: 0.6 }}>· {taskType}</span>}
    </span>
  );
}

function Message({ msg }) {
  return (
    <div className={`message ${msg.role}`}>
      <div className="message-meta">
        {msg.role === "user" ? "you" : (
          <ModelBadge model={msg.model} taskType={msg.taskType} />
        )}
        <span>{msg.time}</span>
      </div>
      <div className="message-bubble">
        {msg.content.split("```").map((part, i) =>
          i % 2 === 1
            ? <pre key={i}><code>{part.trim()}</code></pre>
            : <span key={i}>{part}</span>
        )}
      </div>
      {msg.tools?.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
          ⚙ tools used: {msg.tools.join(", ")}
        </div>
      )}
    </div>
  );
}

export default function Chat({ forceModel }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: "user", content: text, time: now() };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    const history = messages.map(m => ({ role: m.role, content: m.content }));

    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history, force_model: forceModel || null }),
      });
      const data = await res.json();

      setMessages(prev => [...prev, {
        role: "assistant",
        content: data.response,
        model: data.model_used,
        taskType: data.task_type,
        tools: data.tool_calls?.map(t => t.tool) || [],
        time: now(),
      }]);
    } catch (e) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: `Connection error: ${e.message}. Is the backend running? (python main.py)`,
        time: now(),
      }]);
    } finally {
      setLoading(false);
    }
  }

  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function autoResize(e) {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }

  return (
    <div className="chat-container">
      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <div className="chat-empty-glyph">⬡</div>
            <div className="chat-empty-title">Asrār is ready</div>
            <div className="chat-empty-hint">
              Ask anything — browse the web, work on files,<br />
              run commands, diagnose your PC.
            </div>
          </div>
        ) : (
          messages.map((m, i) => <Message key={i} msg={m} />)
        )}
        {loading && (
          <div className="message assistant">
            <div className="typing-indicator">
              <div className="typing-dot" />
              <div className="typing-dot" />
              <div className="typing-dot" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-area">
        <div className="chat-input-row">
          <textarea
            ref={textareaRef}
            className="chat-textarea"
            placeholder="Ask Asrār anything..."
            value={input}
            onChange={e => { setInput(e.target.value); autoResize(e); }}
            onKeyDown={onKey}
            rows={1}
          />
          <button className="send-btn" onClick={send} disabled={!input.trim() || loading}>
            ↑
          </button>
        </div>
        <div className="input-hint">Enter to send · Shift+Enter for new line</div>
      </div>
    </div>
  );
}
